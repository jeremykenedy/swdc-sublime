# Copyright (c) 2018 by Software.com

from datetime import datetime, timezone, timedelta
from threading import Thread, Timer
from queue import Queue
import math
import http
import json
import os
import urllib
import subprocess
import sublime_plugin, sublime

VERSION = '0.1.0'
PM_URL = 'localhost:19234'
USER_AGENT = 'Software.com Sublime Plugin v' + VERSION
LOGGING = True
DEFAULT_DURATION = 60
was_message_shown = False
was_pm_message_shown = False
was_new_version_shown = False
downloadingPM = False
PM_BUCKET = "https://s3-us-west-1.amazonaws.com/swdc-plugin-manager/"
PLUGIN_YML_URL = "https://s3-us-west-1.amazonaws.com/swdc-plugins/plugins.yml"
PM_NAME = "software-plugin-manager"
NO_PM_FOUND_MSG = "We are having trouble sending data to Software.com. The Plugin Manager may not be installed. Would you like to download it now?"
PLUGIN_TO_PM_ERROR_MSG = "We are having trouble sending data to Software.com. Please make sure the Plugin Manager is running and logged on."
PLUGIN_UPDATE_AVAILABLE_MSG = "A new version of Software (%s) is available. You may download the new version at Software.com."

# log the message.
def log(message):
    if LOGGING is False:
        return

    print(message)

# get the url + PM name to download the PM
def getFileUrl():
    fileUrl = PM_BUCKET + PM_NAME
    if (os.name == 'nt'):
        fileUrl += ".exe"
    elif (os.name == 'posix'):
        fileUrl += ".dmg"
    else:
        fileUrl += ".deb"

    return fileUrl

def getDownloadPath():
    downloadPath = os.environ['HOME']
    if (os.name == 'nt'):
        downloadPath += "\\Desktop\\"
    else:
        downloadPath += "/Desktop/"

    return downloadPath


# get the filename we're going to use for the PM download.
def getDownloadFilePathName():
    downloadFilePathName = getDownloadPath()
    if (os.name == 'nt'):
        downloadFilePathName += PM_NAME + ".exe"
    elif (os.name == 'posix'):
        downloadFilePathName += PM_NAME + ".dmg"
    else:
        downloadFilePathName += PM_NAME + ".deb"

    return downloadFilePathName
     
# get the directory path where the PM should be installed
def getPmInstallDirectoryPath():
    if (os.name == 'nt'):
        return os.environ['HOME'] + "\\AppData\\Programs"
    elif (os.name == 'posix'):
        return "/Applications"
    else:
        return "/user/lib"

# check if the PM was installed or not
def hasPluginInstalled():
    installDir = getPmInstallDirectoryPath()

    for file in os.listdir(installDir):
        pathname = os.path.join(installDir, file)
        if (os.path.isfile(pathname) and file.lower().startswith("software")):
            return True

    return False

# post the json data
def post_json(json_data):
    global was_message_shown
    global was_pm_message_shown
    global was_new_version_shown

    try:
        headers = {'Content-type': 'application/json', 'User-Agent': USER_AGENT}
        connection = http.client.HTTPConnection(PM_URL)

        log('Software.com: Sending: %s' % json_data)
        connection.request('POST', '/api/v1/data', json_data, headers)

        response = connection.getresponse()

        # reset the source data.
        PluginData.reset_source_data()

        # log('Software.com: Response (%d): %s' % (response.status, response.read().decode('utf-8')))
        was_message_shown = False
        return response
    except (http.client.HTTPException, ConnectionError) as ex:
        foundPmBinary = hasPluginInstalled()
        if (not was_pm_message_shown and not foundPmBinary):
            was_pm_message_shown = True
            # ask to download the plugin manager
            clickAction = sublime.ok_cancel_dialog(NO_PM_FOUND_MSG, "Download")
            if (clickAction == True):
                thread = DownloadPM()
                thread.start()
            return

        log('Software.com: Network error: %s' % ex)

        if (foundPmBinary and not downloadingPM and not was_message_shown):
            sublime.message_dialog(PLUGIN_TO_PM_ERROR_MSG)
            was_message_shown = True

    # check to see if there's a new plugin version or now
    if (not downloadingPM and not was_new_version_shown):
        updateThread = CheckForUpdates()
        updateThread.start()


class BackgroundWorker():
    def __init__(self, threads_count, target_func):
        self.queue = Queue(maxsize=0)
        self.target_func = target_func
        self.threads = []

        for i in range(threads_count):
            thread = Thread(target=self.worker, daemon=True)
            thread.start()
            self.threads.append(thread)

    def worker(self):
        while True:
            self.target_func(self.queue.get())
            self.queue.task_done()


class DownloadPM(Thread):

    # download the PM and install it....
    def run(self):
        downloadingPM = True
        saveAs = getDownloadFilePathName()
        url = getFileUrl()
        downloadPath = getDownloadPath()

        sublime.status_message("Downlaoding Plugin Manager")

        try:
            urllib.urlretrieve(url, saveAs)
        except AttributeError:
            urllib.request.urlretrieve(url, saveAs)

        sublime.status_message("Finished downloading the Plugin Manager.")

        if (os.name == 'posix'):
            # open the .dmg
            subprocess.Popen(['open', saveAs], stdout=subprocess.PIPE)
        else:
            # open the .deb or .exe
            subprocess.Popen([saveAs], stdout=subprocess.PIPE)

# fetch the plugins.yml to find out if there's a new plugin version to download.
class CheckForUpdates(Thread):

    def run(self):
        with urllib.request.urlopen(PLUGIN_YML_URL) as response:
            ymldata = response.read().decode("utf-8")

            if (ymldata is not None):
                ymllines = ymldata.splitlines()
                # look for "sublime-version"
                if (ymllines and len(ymllines) > 0):
                    for versionline in ymllines:
                        if (versionline.startswith("sublime-version")):
                            versionparts = versionline.split()
                            availableversion = versionparts[1].strip()
                            if (len(versionparts) == 2 and VERSION != availableversion):
                                # alert the user that there's a new version
                                was_new_version_shown = True
                                sublime.message_dialog(PLUGIN_UPDATE_AVAILABLE_MSG % availableversion)
                            # break out, we found the sublime version line
                            break


class PluginData():
    __slots__ = ('source', 'type', 'data', 'start', 'end', 'send_timer', 'project', 'pluginId', 'version')
    convert_to_seconds = ('start', 'end')
    json_ignore = ('send_timer',)
    background_worker = BackgroundWorker(1, post_json)
    active_datas = dict()

    def __init__(self, project):
        self.source = dict()
        self.type = 'Events'
        self.data = 0
        self.start = datetime.utcnow()
        self.end = self.start + timedelta(seconds=60)
        self.send_timer = None
        self.project = project
        self.pluginId = 1
        self.version = VERSION

    def json(self):

        if self.project['directory'] == 'None':
            self.project = None

        dict_data = {key: getattr(self, key, None)
                     for key in self.__slots__ if key not in self.json_ignore}

        for key in self.convert_to_seconds:
            dict_data[key] = int(round(dict_data[key]
                                       .replace(tzinfo=timezone.utc).timestamp()))

        return json.dumps(dict_data)

    def send(self):
        if PluginData.background_worker is not None and self.hasData():
            PluginData.background_worker.queue.put(self.json())

    # check if we have data
    def hasData(self):
        for fileName in self.source:
            fileInfo = self.source[fileName]
            if (fileInfo['close'] > 0 or
                fileInfo['open'] > 0 or
                fileInfo['paste'] > 0 or
                fileInfo['delete'] > 0 or
                fileInfo['keys'] > 0):
                return True
        return False

    @staticmethod
    def reset_source_data():
        for dir in PluginData.active_datas:
            keystrokeCountObj = PluginData.active_datas[dir]
            if keystrokeCountObj is not None:
                keystrokeCountObj.source = dict()
                keystrokeCountObj.data = 0
                keystrokeCountObj.start = datetime.utcnow()
                keystrokeCountObj.end = keystrokeCountObj.start + timedelta(seconds=60)

    @staticmethod
    def get_active_data(view):
        return_data = None
        if view is None or view.window() is None:
            return return_data

        fileName = view.file_name()
        if fileName is None:
            return return_data

        sublime_variables = view.window().extract_variables()
        project = dict()

        #
        projectFolder = 'None'

        # set the project folder
        if 'folder' in sublime_variables:
            projectFolder = sublime_variables['folder']
        elif 'file_path' in sublime_variables:
            projectFolder = sublime_variables['file_path']

        # if we have a valid project folder, set the project name from it
        if projectFolder != 'None':
            project['directory'] = projectFolder
            if 'project_name' in sublime_variables:
                project['name'] = sublime_variables['project_name']
            else:
                # use last file name in the folder as the project name
                projectNameIdx = projectFolder.rfind('/')
                if projectNameIdx > -1:
                    projectName = projectFolder[projectNameIdx + 1:]
                    project['name'] = projectName
        else:
            project['directory'] = 'None'

        old_active_data = None
        if project['directory'] in PluginData.active_datas:
            old_active_data = PluginData.active_datas[project['directory']]

        if old_active_data is None or datetime.utcnow() > old_active_data.end:
            new_active_data = PluginData(project)
            new_active_data.send_timer = Timer(DEFAULT_DURATION + 1,
                                               new_active_data.send)
            new_active_data.send_timer.start()

            PluginData.active_datas[project['directory']] = new_active_data
            return_data = new_active_data
        else:
            return_data = old_active_data

        fileInfoData = PluginData.get_file_info_and_initialize_if_none(return_data, fileName)

        return return_data

    @staticmethod
    def get_existing_file_info(fileName):

        # Get the FileInfo object within the KeystrokesCount object
        # based on the specified fileName.
        for dir in PluginData.active_datas:
            keystrokeCountObj = PluginData.active_datas[dir]
            if keystrokeCountObj is not None:
                hasExistingKeystrokeObj = True
                # we have a keystroke count object, get the fileInfo
                if keystrokeCountObj.source is not None and fileName in keystrokeCountObj.source:
                    return keystrokeCountObj.source[fileName]

        return None

    @staticmethod
    def send_all_datas():
        for dir in PluginData.active_datas:
            PluginData.active_datas[dir].send()

    @staticmethod
    def initialize_file_info(keystrokeCount, fileName):
        if keystrokeCount is None:
            return

        if fileName is None or fileName == '':
            fileName is 'None'
        
        # create the new FileInfo, which will contain a dictionary
        # of fileName and it's metrics
        fileInfoData = PluginData.get_existing_file_info(fileName)

        if fileInfoData is None:
            fileInfoData = dict()
            fileInfoData['keys'] = 0
            fileInfoData['paste'] = 0
            fileInfoData['open'] = 0
            fileInfoData['close'] = 0
            fileInfoData['length'] = 0
            fileInfoData['delete'] = 0
            keystrokeCount.source[fileName] = fileInfoData

    @staticmethod
    def get_file_info_and_initialize_if_none(keystrokeCount, fileName):
        fileInfoData = PluginData.get_existing_file_info(fileName)
        if fileInfoData is None:
            PluginData.initialize_file_info(keystrokeCount, fileName)
            fileInfoData = PluginData.get_existing_file_info(fileName)

        return fileInfoData

# Runs once instance per view (i.e. tab, or single file window)window..
class EventListener(sublime_plugin.EventListener):
    def on_load(self, view):
        fileName = view.file_name()

        active_data = PluginData.get_active_data(view)

        # get the file info to increment the open metric
        fileInfoData = PluginData.get_file_info_and_initialize_if_none(active_data, fileName)
        if fileInfoData is None:
            return

        fileSize = view.size()
        fileInfoData['length'] = fileSize

        # we have the fileinfo, update the metric
        fileInfoData['open'] = fileInfoData['open'] + 1
        log('Software.com: opened file %s' % fileName)

    def on_close(self, view):
        fileName = view.file_name()

        active_data = PluginData.get_active_data(view)

        # get the file info to increment the close metric
        fileInfoData = PluginData.get_file_info_and_initialize_if_none(active_data, fileName)
        if fileInfoData is None:
            return

        fileSize = view.size()
        fileInfoData['length'] = fileSize

        # we have the fileInfo, update the metric
        fileInfoData['close'] = fileInfoData['close'] + 1
        log('Software.com: closed file %s' % fileName)

    def on_modified(self, view):
        # get active data will create the file info if it doesn't exist
        active_data = PluginData.get_active_data(view)
        if active_data is None:
            return

        # add the count for the file
        fileName = view.file_name()
        fileInfoData = PluginData.get_file_info_and_initialize_if_none(active_data, fileName)
        if fileInfoData is None:
            return

        fileSize = view.size()
        # subtract the current size of the file from what we had before
        # we'll know whether it's a delete, copy+paste, or kpm
        currLen = fileInfoData['length']

        charCountDiff = 0
        
        if currLen > 0:
            charCountDiff = fileSize - currLen
        
        fileInfoData['length'] = fileSize
        if charCountDiff > 1:
            fileInfoData['paste'] = fileInfoData['paste'] + charCountDiff
            log('Software.com: copy and pasted incremented')
        elif charCountDiff < 0:
            fileInfoData['delete'] = fileInfoData['delete'] + 1
            log('Software.com: delete incremented')
        else:
            fileInfoData['keys'] = fileInfoData['keys'] + 1
            active_data.data = active_data.data + 1
            log('Software.com: KPM incremented')


def plugin_loaded():
    log('Software.com: Loaded v%s' % VERSION)


def plugin_unloaded():
    PluginData.send_all_datas()
    PluginData.background_worker.queue.join()
