# Copyright (c) 2018 by Software.com

from datetime import datetime
from threading import Thread, Timer, Event
import webbrowser
import uuid
import time
import json
import math
import os
import sublime_plugin, sublime
from .SoftwareHttp import *
from .SoftwareUtil import *

# Constants
DASHBOARD_KEYMAP_MSG = "⚠️Code Time ctrl+alt+o"
SECONDS_PER_HOUR = 60 * 60
LONG_THRESHOLD_HOURS = 12
SHORT_THRESHOLD_HOURS = 4
NO_TOKEN_THRESHOLD_HOURS = 2
LOGIN_LABEL = "Log in"

fetchingUserFromToken = False
fetchingKpmData = False

# launch the browser with either the dashboard or the login
def launchDashboard():
    software_settings = sublime.load_settings("Software.sublime_settings")
    
    webUrl = software_settings.get("software_dashboard_url", "https://app.software.com")
    existingJwt = getItem("jwt")
    tokenVal = getItem("token")
    userIsAuthenticated = isAuthenticated()
    addedToken = False

    if (tokenVal is None):
        tokenVal = createToken()
        # add it to the session file
        setItem("token", tokenVal)
        addedToken = True
    elif (existingJwt is None or not userIsAuthenticated):
        addedToken = True

    if (addedToken):
        webUrl += "/onboarding?token=" + tokenVal


    webbrowser.open(webUrl)

# store the payload offline
def storePayload(payload):
    # append payload to software data store file
    dataStoreFile = getSoftwareDataStoreFile()

    with open(dataStoreFile, "a") as dsFile:
        dsFile.write(payload + "\n")

def checkOnline():
    # non-authenticated ping, no need to set the Authorization header
    response = requestIt("GET", "/ping", None)
    if (isResponsOk(response)):
        return True
    else:
        return False

# send the data that has been saved offline
def sendOfflineData():
    existingJwt = getItem("jwt")

    # no need to try to send the offline data if we don't have an auth token
    if (existingJwt is None):
        return

    # send the offline data
    dataStoreFile = getSoftwareDataStoreFile()

    if (os.path.exists(dataStoreFile)):
        payloads = []

        try:
            with open(dataStoreFile) as fp:
                for line in fp:
                    if (line and line.strip()):
                        line = line.rstrip()
                        # convert to object
                        json_obj = json.loads(line)
                        # convert to json to send
                        payloads.append(json_obj)
        except Exception:
            log("Unable to read offline data file %s" % dataStoreFile)

        if (payloads):
            response = requestIt("POST", "/data/batch", json.dumps(payloads))

            if (isResponsOk(response) or isUserDeactivated(response)):
                os.remove(dataStoreFile)

def chekUserAuthenticationStatus():
    serverAvailable = checkOnline()
    authenticated = isAuthenticated()
    pastThresholdTime = isPastTimeThreshold()
    existingJwt = getItem("jwt")
    existingToken = getItem("token")

    initiateCheckTokenAvailability = True

    # show the dialog if we don't have a token yet,
    # or if we do have a token but no jwt token then
    # show it every 4 hours until we get a jwt token

    if (serverAvailable and not authenticated and pastThresholdTime):

        # remove the jwt so we can re-establish a connection since we're not authenticated
        # setItem("jwt", None)

        # set the last update time so we don't try to ask too frequently
        setItem("sublime_lastUpdateTime", round(time.time()))
        confirmWindowOpen = True
        infoMsg = "To see your coding data in Code Time, please log in to your account."
        clickAction = sublime.ok_cancel_dialog(infoMsg, LOGIN_LABEL)
        if (clickAction):
            # launch the login view
            launchDashboard()
    elif (not authenticated):
        # show the Software.com message
        showStatus(DASHBOARD_KEYMAP_MSG)
    else:
        initiateCheckTokenAvailability = False

    existingToken = getItem("token")
    if (existingToken is not None and initiateCheckTokenAvailability is True):
        # start the token availability timer
        tokenAvailabilityTimer = Timer(60, checkTokenAvailability)
        tokenAvailabilityTimer.start()

def isAuthenticated():
    tokenVal = getItem('token')
    jwtVal = getItem('jwt')

    if (tokenVal is None or jwtVal is None):
        showStatus(DASHBOARD_KEYMAP_MSG)
        return False

    response = requestIt("GET", "/users/ping", None)
    if (isResponsOk(response)):
        return True
    elif (isUserDeactivated(response)):
        return False
    else:
        showStatus(DASHBOARD_KEYMAP_MSG)
        return False

# check if we can update the user if they need to authenticate or not
def isPastTimeThreshold():
    existingJwt = getItem('jwt')

    thresholdHoursBeforeCheckingAgain = LONG_THRESHOLD_HOURS
    if (existingJwt is None):
        existingToken = getItem("token")
        if (existingToken is None):
            thresholdHoursBeforeCheckingAgain = NO_TOKEN_THRESHOLD_HOURS
        else:
            thresholdHoursBeforeCheckingAgain = SHORT_THRESHOLD_HOURS

    lastUpdateTime = getItem("sublime_lastUpdateTime")
    if (lastUpdateTime is None):
        lastUpdateTime = 0

    timeDiffSinceUpdate = round(time.time()) - int(lastUpdateTime)

    threshold = SECONDS_PER_HOUR * thresholdHoursBeforeCheckingAgain

    if (timeDiffSinceUpdate < threshold):
        return False

    return True

#
# check if the token is found to establish an authenticated session
#
def checkTokenAvailability():
    global fetchingUserFromToken

    tokenVal = getItem("token")
    jwtVal = getItem("jwt")
    isDeactivated = False

    foundJwt = False
    if (tokenVal is not None):
        api = '/users/plugin/confirm?token=' + tokenVal
        response = requestIt("GET", api, None)

        isDeactivated = isUserDeactivated(response)

        if (isResponsOk(response)):

            json_obj = json.loads(response.read().decode('utf-8'))

            jwt = json_obj.get("jwt", None)
            user = json_obj.get("user", None)
            if (jwt is not None):
                setItem("jwt", jwt)
                setItem("user", user)
                setItem("sublime_lastUpdateTime", round(time.time()))
                foundJwt = True
            else:
                # check if there's a message
                message = json_obj.get("message", None)
                if (message is not None):
                    log("Code Time: Failed to retrieve session token, reason: \"%s\"" % message)
        elif (isUnauthenticated(response) and isDeactivated is False):
            # not deactivated but unauthenticated
            showStatus(DASHBOARD_KEYMAP_MSG)

    if (not foundJwt and jwtVal is None and isDeactivated is False):
        # start the token availability timer again
        tokenAvailabilityTimer = Timer(120, checkTokenAvailability)
        tokenAvailabilityTimer.start()
        showStatus(DASHBOARD_KEYMAP_MSG)

#
# Fetch and display the daily KPM info.
#
def fetchDailyKpmSessionInfo():
    global fetchingKpmData

    isDeactivated = False

    if (fetchingKpmData is False):

        fetchingKpmData = True

        # send in the start of the day in seconds
        today = datetime.now()
        today = today.replace(hour=0, minute=0, second=0, microsecond=0)
        fromSeconds = round(today.timestamp())

        # api to fetch the session kpm info
        api = '/sessions?summary=true'
        response = requestIt("GET", api, None)

        fetchingKpmData = False

        isDeactivated = isUserDeactivated(response)

        if (isResponsOk(response)):
            sessions = json.loads(response.read().decode('utf-8'))
            # i.e.
            # {'sessionMinAvg': 0, 'inFlow': False, 'currentSessionMinutes': 23.983333333333334, 'lastKpm': 0, 'currentSessionGoalPercent': None}
            # but should be...
            # {'sessionMinAvg': 0, 'inFlow': False, 'currentSessionMinutes': 23.983333333333334, 'lastKpm': 0, 'currentSessionGoalPercent': 0.44}

            avgKpmStr = "0"
            try:
                avgKpmStr = '{:1.0f}'.format(sessions.get("lastKpm", 0))
            except Exception:
                avgKpmStr = "0"

            currentSessionMinutes = 0
            try:
                currentSessionMinutes = int(sessions.get("currentSessionMinutes", 0))
            except Exception:
                currentSessionMinutes = 0

            sessionMinGoalPercent = 0.0
            try:
                if (sessions.get("currentSessionGoalPercent") is not None):
                    sessionMinGoalPercent = float(sessions.get("currentSessionGoalPercent", 0.0))
            except Exception:
                sessionMinGoalPercent = 0.0

            currentDayMinutes = 0
            try:
                currentDayMinutes = int(sessions.get("currentDayMinutes", 0))
            except Exception:
                currentDayMinutes = 0
            averageDailyMinutes = 0
            try:
                averageDailyMinutes = int(sessions.get("averageDailyMinutes", 0))
            except Exception:
                averageDailyMinutes = 0
            
            currentSessionTime = humanizeMinutes(currentSessionMinutes)
            currentDayTime = humanizeMinutes(currentDayMinutes)
            averageDailyTime = humanizeMinutes(averageDailyMinutes)

            inFlowIcon = ""
            if (currentDayMinutes > averageDailyMinutes):
                inFlowIcon = "🚀"

            statusMsg = "Code time: " + inFlowIcon + "" + currentDayTime
            if (averageDailyMinutes > 0):
                statusMsg += " | Avg:" + averageDailyTime

            showStatus(statusMsg)
        elif (isUnauthenticated(response) and isDeactivated is False):
            chekUserAuthenticationStatus()

    # fetch the daily kpm session info in 1 minute
    if (isDeactivated is False):
        kpmReFetchTimer = Timer(60, fetchDailyKpmSessionInfo)
        kpmReFetchTimer.start()

def humanizeMinutes(minutes):
    minutes = int(minutes)
    humanizedStr = ""
    if (minutes == 60):
        humanizedStr = "1 hr"
    elif (minutes > 60):
        floatMin = (minutes / 60)
        if (floatMin % 1 == 0):
            # don't show zeros after the decimal
            humanizedStr = '{:4.0f}'.format(floatMin) + " hrs"
        else:
            # at least 4 chars (including the dot) with 2 after the dec point
            humanizedStr = '{:4.1f}'.format(round(floatMin, 1)) + " hrs"
    elif (minutes == 1):
        humanizedStr = "1 min"
    else:
        humanizedStr = '{:1.0f}'.format(minutes) + " min"

    return humanizedStr

# crate a uuid token to establish a connection
def createToken():
    # return os.urandom(16).encode('hex')
    uid = uuid.uuid4()
    return uid.hex

def handlKpmClickedEvent():
    launchDashboard()



