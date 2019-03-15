# Copyright (c) 2018 by Software.com
from threading import Thread, Timer, Event
import os
import json
import time
import sublime_plugin, sublime
import sys
import uuid
import platform
import re, uuid
import webbrowser
from urllib.parse import quote_plus
from subprocess import Popen, PIPE
from .SoftwareHttp import *

# the plugin version
VERSION = '0.7.4'
PLUGIN_ID = 1
SETTINGS_FILE = 'Software.sublime_settings'
SETTINGS = {}

runningResourceCmd = False

# log the message
def log(message):
    software_settings = sublime.load_settings("Software.sublime_settings")
    if (software_settings.get("software_logging_on", True)):
        print(message)

def getUrlEndpoint():
    software_settings = sublime.load_settings("Software.sublime_settings")
    return software_settings.get("software_dashboard_url", "https://app.software.com")

def getOsUsername():
    homedir = os.path.expanduser('~')
    username = os.path.basename(homedir)

    if (username is None or username == ""):
        username = os.environ.get("USER")
    
    return username

def getTimezone():
    timezone = ""
    try:
        timezone = time.strftime('%Z')
    except Exception:
        timezone = time.tzname[0]
    return timezone

# fetch a value from the .software/sesion.json file
def getItem(key):
    jsonObj = getSoftwareSessionAsJson()

    # return a default of None if key isn't found
    val = jsonObj.get(key, None)

    return val

# get an item from the session json file
def setItem(key, value):
    jsonObj = getSoftwareSessionAsJson()
    jsonObj[key] = value

    content = json.dumps(jsonObj)

    sessionFile = getSoftwareSessionFile()
    with open(sessionFile, 'w') as f:
        f.write(content)

# store the payload offline
def storePayload(payload):
    # append payload to software data store file
    dataStoreFile = getSoftwareDataStoreFile()

    with open(dataStoreFile, "a") as dsFile:
        dsFile.write(payload + "\n")

def getSoftwareSessionAsJson():
    try:
        with open(getSoftwareSessionFile()) as sessionFile:
            return json.load(sessionFile)
    except Exception:
        return {}

def getSoftwareSessionFile():
    file = getSoftwareDir()
    return os.path.join(file, 'session.json')

def getSoftwareDataStoreFile():
    file = getSoftwareDir()
    return os.path.join(file, 'data.json')

def getSoftwareDir():
    softwareDataDir = os.path.expanduser('~')
    softwareDataDir = os.path.join(softwareDataDir, '.software')
    os.makedirs(softwareDataDir, exist_ok=True)
    return softwareDataDir

def getDashboardFile():
    file = getSoftwareDir()
    return os.path.join(file, 'CodeTime.txt')

# execute the applescript command
def runComand(cmd, args):
    p = Popen(args, stdin=PIPE, stdout=PIPE, stderr=PIPE)
    stdout, stderr = p.communicate(cmd)
    return stdout.decode('utf-8').strip()

def getItunesTrackState():
    script = '''
        tell application "iTunes" to get player state
        '''
    try:
        cmd = script.encode('latin-1')
        result = runComand(cmd, ['osascript', '-'])
        return result
    except Exception as e:
        log("exception getting track state: %s " % e)
        # no music found playing
        return "stopped"

def getSpotifyTrackState():
    script = '''
        tell application "Spotify" to get player state
        '''
    try:
        cmd = script.encode('latin-1')
        result = runComand(cmd, ['osascript', '-'])
        return result
    except Exception as e:
        log("exception getting track state: %s " % e)
        # no music found playing
        return "stopped"


# get the current track playing (spotify or itunes)
def getTrackInfo():
    if sys.platform == "darwin":
        return getMacTrackInfo()
    elif sys.platform == "win32":
        # not supported on other platforms yet
        return getWinTrackInfo()
    else:
        # linux not supported yet
        return {}

# windows
def getWinTrackInfo():
    # not supported on other platforms yet
    return {}

# OS X
def getMacTrackInfo():
    script = '''
        on buildItunesRecord(appState)
            tell application "iTunes"
                set track_artist to artist of current track
                set track_name to name of current track
                set track_genre to genre of current track
                set track_id to database ID of current track
                set track_duration to duration of current track
                set json to "type='itunes';genre='" & track_genre & "';artist='" & track_artist & "';id='" & track_id & "';name='" & track_name & "';state='playing';duration='" & track_duration & "'"
            end tell
            return json
        end buildItunesRecord

        on buildSpotifyRecord(appState)
            tell application "Spotify"
                set track_artist to artist of current track
                set track_name to name of current track
                set track_duration to duration of current track
                set track_id to id of current track
                set track_duration to duration of current track
                set json to "type='spotify';genre='';artist='" & track_artist & "';id='" & track_id & "';name='" & track_name & "';state='playing';duration='" & track_duration & "'"
            end tell
            return json
        end buildSpotifyRecord

        try
            if application "Spotify" is running and application "iTunes" is not running then
                tell application "Spotify" to set spotifyState to (player state as text)
                -- spotify is running and itunes is not
                if (spotifyState is "paused" or spotifyState is "playing") then
                    set jsonRecord to buildSpotifyRecord(spotifyState)
                else
                    set jsonRecord to {}
                end if
            else if application "Spotify" is running and application "iTunes" is running then
                tell application "Spotify" to set spotifyState to (player state as text)
                tell application "iTunes" to set itunesState to (player state as text)
                -- both are running but use spotify as a higher priority
                if spotifyState is "playing" then
                    set jsonRecord to buildSpotifyRecord(spotifyState)
                else if itunesState is "playing" then
                    set jsonRecord to buildItunesRecord(itunesState)
                else if spotifyState is "paused" then
                    set jsonRecord to buildSpotifyRecord(spotifyState)
                else
                    set jsonRecord to {}
                end if
            else if application "iTunes" is running and application "Spotify" is not running then
                tell application "iTunes" to set itunesState to (player state as text)
                set jsonRecord to buildItunesRecord(itunesState)
            else
                set jsonRecord to {}
            end if
            return jsonRecord
        on error
            return {}
        end try
    '''
    try:
        cmd = script.encode('latin-1')
        result = runComand(cmd, ['osascript', '-'])
        result = result.strip('\r\n')
        result = result.replace('"', '')
        result = result.replace('\'', '')

        if (result):
            trackInfo = dict(item.strip().split("=") for item in result.strip().split(";"))
            return trackInfo
        else:
            return {}
    except Exception as e:
        log("exception getting track: %s " % e)
        # no music found playing
        return {}

def runResourceCmd(cmdArgs, rootDir):
    if sys.platform == "darwin": # OS X
        runningResourceCmd = True
        p = Popen(cmdArgs, cwd = rootDir, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        stdout, stderr = p.communicate()
        stdout = stdout.decode('utf-8').strip()
        if (stdout):
            stdout = stdout.strip('\r\n')
            return stdout
        else:
            return ""
    else:
        return ""


def getResourceInfo(rootDir):
    try:
        resourceInfo = {}
        tag = runResourceCmd(['git', 'describe', '--all'], rootDir)

        if (tag):
            resourceInfo['tag'] = tag
        identifier = runResourceCmd(['git', 'config', '--get', 'remote.origin.url'], rootDir)

        if (identifier):
            resourceInfo['identifier'] = identifier
        branch = runResourceCmd(['git', 'symbolic-ref', '--short', 'HEAD'], rootDir)

        if (branch):
            resourceInfo['branch'] = branch
        email = runResourceCmd(['git', 'config', 'user.email'], rootDir)

        if (email):
            resourceInfo['email'] = email
            
        if (resourceInfo.get("identifier") is not None):
            return resourceInfo
        else:
            return {}
    except Exception as e:
        return {}

def checkOnline():
    # non-authenticated ping, no need to set the Authorization header
    response = requestIt("GET", "/ping", None, getItem("jwt"))
    if (isResponsOk(response)):
        return True
    else:
        return False

def refetchUserStatusLazily(tryCountUntilFoundUser):
    currentUserStatus = getUserStatus()
    loggedInUser = currentUserStatus.get("loggedInUser", None)
    if (loggedInUser is not None or tryCountUntilFoundUser <= 0):
        return

    # start the time
    tryCountUntilFoundUser -= 1
    t = Timer(10, refetchUserStatusLazily, [tryCountUntilFoundUser])
    t.start()

def launchLoginUrl():
    software_settings = sublime.load_settings("Software.sublime_settings")
    webUrl = getUrlEndpoint()
    jwt = getItem("jwt")
    webUrl += "/onboarding?token=" + jwt
    webbrowser.open(webUrl)
    refetchUserStatusLazily(10)

def launchWebDashboardUrl():
    software_settings = sublime.load_settings("Software.sublime_settings")
    webUrl = getUrlEndpoint()
    webbrowser.open(webUrl)

def isMac():
    if sys.platform == "darwin":
        return True
    return False

def isWindows():
    if sys.platform == "win32":
        return True
    return False

def fetchCodeTimeMetrics():
    islinux = "true"
    if isWindows() is True or isMac() is True:
        islinux = "false"
    api = '/dashboard?linux=' + islinux
    response = requestIt("GET", api, None, getItem("jwt"))
    content = response.read().decode('utf-8')
    file = getDashboardFile()
    with open(file, 'w', encoding='utf-8') as f:
        f.write(content)

def launchCodeTimeMetrics():
    fetchCodeTimeMetrics()
    file = getDashboardFile()
    sublime.active_window().open_file(file)

def getAppJwt():
    serverAvailable = checkOnline()
    if (serverAvailable):
        now = round(time.time())
        api = "/data/apptoken?token=" + str(now)
        response = requestIt("GET", api, None, None)
        if (response is not None):
            responseObjStr = response.read().decode('utf-8')
            try:
                responseObj = json.loads(responseObjStr)
                appJwt = responseObj.get("jwt", None)
                if (appJwt is not None):
                    return appJwt
            except Exception as ex:
                log("Code Time: Unable to retrieve app token: %s" % ex)
    return None

# crate a uuid token to establish a connection
def createToken():
    # return os.urandom(16).encode('hex')
    uid = uuid.uuid4()
    return uid.hex

def createAnonymousUser(serverAvailable):
    appJwt = getAppJwt()
    if (serverAvailable and appJwt):
        username = getOsUsername()
        timezone = getTimezone()

        payload = {}
        payload["username"] = username
        payload["timezone"] = timezone

        api = "/data/onboard"
        try:
            response = requestIt("POST", api, json.dumps(payload), appJwt)

            if (response is not None and isResponsOk(response)):
                try:
                    responseObj = json.loads(response.read().decode('utf-8'))
                    jwt = responseObj.get("jwt", None)
                    setItem("jwt", jwt)
                    return
                except Exception as ex:
                    log("Code Time: Unable to retrieve plugin accounts response: %s" % ex)
        except Exception as ex:
            log("Code Time: Unable to complete anonymous user creation: %s" % ex)

def isLoggedOn(serverAvailable):
    jwt = getItem("jwt")
    if (serverAvailable):
        api = "/users/plugin/state"
        response = requestIt("GET", api, None, jwt)

        responseOk = isResponsOk(response)
        if (responseOk is True):
            try:
                responseObj = json.loads(response.read().decode('utf-8'))
                
                state = responseObj.get("state", None)
                if (state is not None and state == "OK"):
                    email = responseObj.get("emai", None)
                    setItem("name", email)
                    pluginJwt = responseObj.get("jwt", None)
                    if (pluginJwt is not None and pluginJwt != jwt):
                        setItem("jwt", pluginJwt)

                    # state is ok, return True
                    return True
                elif (state is not None and state != "ANONYMOUS"):
                    setItem("jwt", None)

            except Exception as ex:
                log("Code Time: Unable to retrieve logged on response: %s" % ex)
        elif (responseOk is False):
            setItem("jwt", None)

    setItem("name", None)
    return False


def getUserStatus():
    global SETTINGS

    getOsUsername()

    SETTINGS = sublime.load_settings(SETTINGS_FILE)

    jwt = getItem("jwt")
    serverAvailable = checkOnline()

    # initialize an anonymous user if we don't have a jwt
    if (jwt is None):
        # create the anonymous user
        createAnonymousUser(serverAvailable)

    # check if they're logged in or not
    loggedOn = isLoggedOn(serverAvailable)

    # the jwt may have been nulled out
    jwt = getItem("jwt");
    if (jwt is None):
        # create an anonymous user
        createAnonymousUser(serverAvailable)
    
    SETTINGS.set("logged_on", loggedOn)
    currentUserStatus = {}
    currentUserStatus["loggedOn"] = loggedOn

    return currentUserStatus



