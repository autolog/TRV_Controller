#! /usr/bin/env python
# -*- coding: utf-8 -*-
#
# TRV Controller © Autolog 2019

try:
    # noinspection PyUnresolvedReferences
    import indigo
except ImportError:
    pass

import csv
import collections
import datetime
import logging
import platform
import Queue
import operator
import threading
import time
import sys
import xml.etree.ElementTree as ET

from constants import *
from trvHandler import ThreadTrvHandler
# from polling import ThreadPolling

def convertListToHexStr(byteList):
    return ' '.join(["%02X" % byte for byte in byteList])

# Convert str('HH:MM' to INT(seconds))
def secondsFromHHMM(hhmm):
    hh = int(hhmm[0:2])
    mm = int(hhmm[3:5])
    seconds = (hh * 3600) + (mm * 60) 
    return seconds

# Calculate number of seconds until 15 minutes after next midnight 
def calculateSecondsUntilSchedulesRestated():

    tomorrow = datetime.datetime.now() + datetime.timedelta(1)
    midnight = datetime.datetime(year=tomorrow.year, month=tomorrow.month, 
                    day=tomorrow.day, hour=0, minute=0, second=0)
    secondsToMidnight = int((midnight - datetime.datetime.now()).seconds)  # Seconds to midnight

    secondsSinceMidnight = (24 * 60 * 60) - secondsToMidnight

    secondsInFifteenMinutes = (5 * 60)  # 5 minutes in seconds

    secondsUntilSchedulesRestated = secondsToMidnight + secondsInFifteenMinutes  # Calculate number of seconds until 5 minutes after next midnight

    # secondsUntilSchedulesRestated = 60  # TESTING = 1 Minute
    return secondsUntilSchedulesRestated

# noinspection PyUnresolvedReferences
class Plugin(indigo.PluginBase):

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):

        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        # Initialise dictionary to store plugin Globals
        self.globals = dict()

        self.globals['zwave'] = dict()
        self.globals['zwave']['addressToDevice'] = dict()
        self.globals['zwave']['WatchList'] = set() # TRVs, Valves and Remotes associated with a TRV Controllers will get added to this SET on TRV Controller device start

        # Initialise Indigo plugin info
        self.globals['pluginInfo'] = dict()
        self.globals['pluginInfo']['pluginId'] = pluginId
        self.globals['pluginInfo']['pluginDisplayName'] = pluginDisplayName
        self.globals['pluginInfo']['pluginVersion'] = pluginVersion
        self.globals['pluginInfo']['path'] = indigo.server.getInstallFolderPath()  # e.g. '/Library/Application Support/Perceptive Automation/Indigo 7.2'
        self.globals['pluginInfo']['apiVersion'] = indigo.server.apiVersion
        self.globals['pluginInfo']['address'] = indigo.server.address

        # Initialise dictionary for debug in plugin Globals
        self.globals['debug'] = dict()
        self.globals['debug']['general'] = logging.INFO  # For general debugging of the main thread
        self.globals['debug']['trvHandler'] = logging.INFO  # For debugging TRV handler thread
        self.globals['debug']['methodTrace'] = logging.INFO  # For displaying method invocations i.e. trace method
        self.globals['debug']['polling'] = logging.INFO  # For polling debugging

        self.globals['debug']['previousGeneral'] = logging.INFO  # For general debugging of the main thread
        self.globals['debug']['previousTrvHandler'] = logging.INFO  # For debugging TRV handler thread 
        self.globals['debug']['previousMethodTrace'] = logging.INFO  # For displaying method invocations i.e. trace method
        self.globals['debug']['previousPolling'] = logging.INFO  # For polling debugging

        # Setup Logging
        logformat = logging.Formatter('%(asctime)s.%(msecs)03d\t%(levelname)-12s\t%(name)s.%(funcName)-25s %(msg)s', datefmt='%Y-%m-%d %H:%M:%S')
        self.plugin_file_handler.setFormatter(logformat)
        self.plugin_file_handler.setLevel(logging.INFO)  # Master Logging Level for Plugin Log file
        self.indigo_log_handler.setLevel(logging.INFO)   # Logging level for Indigo Event Log
        self.generalLogger = logging.getLogger("Plugin.general")
        self.generalLogger.setLevel(self.globals['debug']['general'])
        self.methodTracer = logging.getLogger("Plugin.method")  
        self.methodTracer.setLevel(self.globals['debug']['methodTrace'])

        # Now logging is set-up, output Initialising Message

        startupMessageUi = '\n'  # Start with a line break
        startupMessageUi += u'{:=^130}\n'.format(' Initializing TRV Controller Plugin ')
        startupMessageUi += u'{:<31} {}\n'.format('Plugin Name:', self.globals['pluginInfo']['pluginDisplayName'])
        startupMessageUi += u'{:<31} {}\n'.format('Plugin Version:', self.globals['pluginInfo']['pluginVersion'])
        startupMessageUi += u'{:<31} {}\n'.format('Plugin ID:', self.globals['pluginInfo']['pluginId'])
        startupMessageUi += u'{:<31} {}\n'.format('Indigo Version:', indigo.server.version)
        startupMessageUi += u'{:<31} {}\n'.format('Indigo API Version:', indigo.server.apiVersion)
        startupMessageUi += u'{:<31} {}\n'.format('Python Version:', sys.version.replace('\n', ''))
        startupMessageUi += u'{:<31} {}\n'.format('Mac OS Version:', platform.mac_ver()[0])
        startupMessageUi += u'{:=^130}\n'.format('')
        self.generalLogger.info(startupMessageUi)

        # Initialise dictionary to store configuration info
        self.globals['config'] = dict()

        # Initialise dictionary to store internal details about TRV Controller devices
        self.globals['trvc'] = dict()

         # Initialise dictionary to store internal details about heating (Boiler) devices and variables
        self.globals['heaterDevices'] = dict()
        self.globals['heaterVariables'] = dict()

        # Initialise dictionary to store lime protection details
        self.globals['limeProtection'] = dict()

        # Initialise dictionary to store message queues
        self.globals['queues'] = dict()
        self.globals['queues']['trvHandler'] = dict()
        # self.globals['queues']['runConcurrentQueue'] = dict()
        self.globals['queues']['initialised'] = False

        # Initialise dictionary to store heating schedules
        self.globals['schedules'] = dict()

        # Initialise count of device updates detected - used for debugging purposes

        self.globals['deviceUpdatedSequenceCount'] = 0

        # Initialise dictionary to store timers
        self.globals['timers'] = dict()
        self.globals['timers']['heaters'] = dict()
        self.globals['timers']['heatingSchedules'] = dict()
        self.globals['timers']['command'] = dict()
        self.globals['timers']['SpiritPolling'] = dict()
        self.globals['timers']['SpiritValveCommands'] = dict()
        self.globals['timers']['advanceCancel'] = dict()
        self.globals['timers']['boost'] = dict()
        self.globals['timers']['raceCondition'] = dict()
        self.globals['timers']['zwaveWakeupCheck'] = dict()

        # Initialise dictionary to store threads
        self.globals['threads'] = dict()
        self.globals['threads']['polling'] = dict()  # There is only one 'polling' thread for all TRV devices
        self.globals['threads']['trvHandler'] = dict()  # There is only one 'trvHandler' thread for all TRV devices

        self.globals['threads']['runConcurrentActive'] = False

        self.globals['lock'] = threading.Lock()
        
        self.globals['devicesToTrvControllerTable'] = dict()

        # Initialise dictionary for constants
        self.globals['constant'] = dict()
        self.globals['constant']['defaultDatetime'] = datetime.datetime.strptime('2000-01-01','%Y-%m-%d')

        # Initialise dictionary for update checking
        self.globals['update'] = dict()

        # Setup dictionary of supported TRV models
        xmlFile = '{}/Plugins/TRV.indigoPlugin/Contents/Resources/supportedThermostatModels.xml'.format(self.globals['pluginInfo']['path'])
        tree = ET.parse(xmlFile)
        root = tree.getroot()
        self.globals['supportedTrvModels'] = dict()
        for model in root.findall('model'):
            trv_model_name = model.get('name')
            self.globals['supportedTrvModels'][trv_model_name] = dict()
            self.globals['supportedTrvModels'][trv_model_name]['supportsWakeup'] = bool(True if model.find('supportsWakeup').text == 'true' else False)
            self.globals['supportedTrvModels'][trv_model_name]['supportsTemperatureReporting'] = bool(True if model.find('supportsTemperatureReporting').text == 'true' else False)
            self.globals['supportedTrvModels'][trv_model_name]['supportsHvacOnOff'] = bool(True if model.find('supportsHvacOnOff').text == 'true' else False)
            self.globals['supportedTrvModels'][trv_model_name]['supportsValveControl'] = bool(True if model.find('supportsValveControl').text == 'true' else False)
            self.globals['supportedTrvModels'][trv_model_name]['supportsManualSetpoint'] = bool(True if model.find('supportsManualSetpoint').text == 'true' else False)
            self.globals['supportedTrvModels'][trv_model_name]['setpointHeatMinimum'] = float(model.find('setpointHeatMinimum').text)
            self.globals['supportedTrvModels'][trv_model_name]['setpointHeatMaximum'] = float(model.find('setpointHeatMaximum').text) 

            # self.generalLogger.error('XML [SUPPORTED TRV MODEL] =\n{}'.format(self.globals['supportedTrvModels'][trv_model_name]))

        # Setup dictionary of fully supported Heat Source Controller Devices
        xmlFile = '{}/Plugins/TRV.indigoPlugin/Contents/Resources/supportedHeatSourceControllers.xml'.format(self.globals['pluginInfo']['path'])
        tree = ET.parse(xmlFile)
        root = tree.getroot()
        self.globals['supportedHeatSourceControllers'] = dict()
        for model in root.findall('model'):
            heat_source_controller_model_name = model.get('name')
            self.globals['supportedHeatSourceControllers'][heat_source_controller_model_name] = ''

            # self.generalLogger.error('XML [SUPPORTED HEAT SOURCE CONTROLLER] =\n{}'.format(heat_source_controller_model_name))

        # Set Plugin Config Values
        self.closedPrefsConfigUi(pluginPrefs, False)
 
    def __del__(self):

        indigo.PluginBase.__del__(self)

    def startup(self):
        self.methodTracer.threaddebug(u'Main Plugin Method')

        indigo.devices.subscribeToChanges()

        # Subscribe to incoming raw Z-Wave command bytes
        indigo.zwave.subscribeToIncoming()

        #Subscribe to outgoing raw Z-Wave command bytes
        indigo.zwave.subscribeToOutgoing()

        # Create trvHandler process queue
        self.globals['queues']['trvHandler'] = Queue.PriorityQueue()  # Used to queue trvHandler commands
        self.globals['queues']['runConcurrentQueue'] = Queue.Queue()  # t.b.a
        self.globals['queues']['initialised'] = True
        
        self.globals['threads']['trvHandler']['event']  = threading.Event()
        self.globals['threads']['trvHandler']['thread'] = ThreadTrvHandler(self.globals, self.globals['threads']['trvHandler']['event'])
        # self.globals['threads']['trvHandler']['thread'].setDaemon(True)
        self.globals['threads']['trvHandler']['thread'].start()

        self.validateActionFlag = dict()

        self.globals['limeProtection'] = dict()
        self.globals['limeProtection']['Requested'] = False
        self.globals['limeProtection']['Active'] = False
        self.globals['limeProtection']['ThermostatList'] = None

        try: 

            secondsUntilSchedulesRestated = calculateSecondsUntilSchedulesRestated()
            self.globals['timers']['reStateSchedules'] = threading.Timer(float(secondsUntilSchedulesRestated), self.restateSchedulesTriggered, [secondsUntilSchedulesRestated])
            self.globals['timers']['reStateSchedules'].setDaemon(True)
            self.globals['timers']['reStateSchedules'].start()

            self.generalLogger.info(u'TRV Controller has calculated the number of seconds until Schedules restated as {}'.format(secondsUntilSchedulesRestated))

        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [Startup]. Line \'{}\' has error=\'{}\''.format(trvcDev.name, sys.exc_traceback.tb_lineno, err))   

        self.generalLogger.info(u'\'TRV Controller\' initialization complete')

    def restateSchedulesTriggered(self, triggeredSeconds):

        try: 
            self.methodTracer.threaddebug(u'Main Plugin Method')

            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_RESTATE_SCHEDULES, None, None])

            secondsUntilSchedulesRestated = calculateSecondsUntilSchedulesRestated()
            self.globals['timers']['reStateSchedules'] = threading.Timer(float(secondsUntilSchedulesRestated), self.restateSchedulesTriggered, [secondsUntilSchedulesRestated])
            self.globals['timers']['reStateSchedules'].setDaemon(True)
            self.globals['timers']['reStateSchedules'].start()

            self.generalLogger.info(u'TRV Controller has calculated the number of seconds until Schedules restated as {}'.format(secondsUntilSchedulesRestated))


        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [restateSchedulesTriggered]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   
        
    def stopConcurrentThread(self):
        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.generalLogger.debug(u'Thread shutdown called')

        self.stopThread = True

    def shutdown(self):
        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.generalLogger.debug(u'Shutdown called')

        self.generalLogger.info(u'\'TRV Controller\' Plugin shutdown complete')

    def getPrefsConfigUiValues(self):
        self.methodTracer.threaddebug(u'Main Plugin Method')

        prefsConfigUiValues = self.pluginPrefs
        if "trvVariableFolderName" not in prefsConfigUiValues:
            prefsConfigUiValues["trvVariableFolderName"] = 'TRV'
        if "limeProtectionEnabled" not in prefsConfigUiValues:
            prefsConfigUiValues["limeProtectionEnabled"] = False
        if "scheduleLimeProtectionId" not in prefsConfigUiValues or prefsConfigUiValues["scheduleLimeProtectionId"] == '':
            prefsConfigUiValues["scheduleLimeProtectionId"] = int(0)  # No schedule defined
        if "disableHeatSourceDeviceListFilter" not in prefsConfigUiValues:
            prefsConfigUiValues["disableHeatSourceDeviceListFilter"] = False

        return prefsConfigUiValues

    def validatePrefsConfigUi(self, valuesDict):
        self.methodTracer.threaddebug(u'Main Plugin Method')

        return True

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        self.methodTracer.threaddebug(u'Main Plugin Method')

        try:

            self.generalLogger.debug(u'\'closePrefsConfigUi\' called with userCancelled = {}'.format(str(userCancelled)))  

            if userCancelled:
                return
            self.globals['config']['disableHeatSourceDeviceListFilter'] = valuesDict.get('disableHeatSourceDeviceListFilter', False)


            self.globals['update']['check'] = bool(valuesDict.get("updateCheck", False))
            self.globals['update']['checkFrequency'] = valuesDict.get("checkFrequency", 'DAILY')

            if self.globals['update']['check']:
                if self.globals['update']['checkFrequency'] == 'WEEKLY':
                    self.globals['update']['checkTimeIncrement'] = (7 * 24 * 60 * 60)  # In seconds
                else:
                    # DAILY 
                    self.globals['update']['checkTimeIncrement'] = (24 * 60 * 60)  # In seconds

            # ### VARIABLE FOLDER ###
            self.globals['config']['trvVariableFolderName'] = valuesDict.get("trvVariableFolderName",'TRV')

            # CSV File Handling (for e.g. Matplotlib plugin)

            self.globals['config']['csvStandardEnabled'] = valuesDict.get("csvStandardEnabled",False)
            self.globals['config']['csvPostgresqlEnabled'] = valuesDict.get("csvPostgresqlEnabled",False)
            self.globals['config']['postgresqlUser'] = valuesDict.get("postgresqlUser",'')
            self.globals['config']['postgresqlPassword'] = valuesDict.get("postgresqlPassword",'')
            self.globals['config']['csvPath'] = valuesDict.get("csvPath",'')
            self.globals['config']['csvPrefix'] = valuesDict.get("csvPrefix",'TRV_Plugin')

            # Create TRV Variable folder name (if required)
            if self.globals['config']['trvVariableFolderName'] == '':
                self.globals['config']['trvVariableFolderId'] = 0  # Not required
            else:
                if self.globals['config']['trvVariableFolderName'] not in indigo.variables.folders:
                    indigo.variables.folder.create(self.globals['config']['trvVariableFolderName'])
                self.globals['config']['trvVariableFolderId'] = indigo.variables.folders.getId(self.globals['config']['trvVariableFolderName'])

            # Check monitoring / debug / filered IP address options  
            self.setDebuggingLevels(valuesDict)

        except StandardError, e:
            self.generalLogger.error(u'closedPrefsConfigUi error detected. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, e))   
            return True

    def setDebuggingLevels(self, valuesDict):
        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.globals['debug']['enabled'] = bool(valuesDict.get("debugEnabled", False))

        self.globals['debug']['general'] = logging.INFO  # For general debugging of the main thread
        self.globals['debug']['trvHandler'] = logging.INFO  # For debugging messages
        self.globals['debug']['methodTrace'] = logging.INFO  # For displaying method invocations i.e. trace method
        self.globals['debug']['polling'] = logging.INFO  # For polling debugging

        if not self.globals['debug']['enabled']:
            self.plugin_file_handler.setLevel(logging.INFO)
        else:
            self.plugin_file_handler.setLevel(logging.THREADDEBUG)

        debugGeneral = bool(valuesDict.get("debugGeneral", False))
        debugTrvHandler = bool(valuesDict.get("debugTrvHandler", False))
        debugMethodTrace = bool(valuesDict.get("debugMethodTrace", False))
        debugPolling = bool(valuesDict.get("debugPolling", False))

        if debugGeneral:
            self.globals['debug']['general'] = logging.DEBUG  # For general debugging of the main thread
            self.generalLogger.setLevel(self.globals['debug']['general'])
        if debugTrvHandler:
            self.globals['debug']['trvHandler'] = logging.DEBUG  # For debugging TRV handler thread
        if debugMethodTrace:
            self.globals['debug']['methodTrace'] = logging.THREADDEBUG  # For displaying method invocations i.e. trace method
        if debugPolling:
            self.globals['debug']['polling'] = logging.DEBUG  # For polling debugging

        self.globals['debug']['active'] = debugGeneral or debugTrvHandler or debugMethodTrace or debugPolling

        if not self.globals['debug']['enabled'] or not self.globals['debug']['active']:
            self.generalLogger.info(u'No debugging requested for TRV plugin')
        else:
            debugTypes = []
            if debugGeneral:
                debugTypes.append('General')
            if debugTrvHandler:
                debugTypes.append('TRV Handler')
            if debugMethodTrace:
                debugTypes.append('Method Trace')
            if debugPolling:
                debugTypes.append('Polling')
            message = self.listActiveDebugging(debugTypes)   
            self.generalLogger.warning(u'The debugging options enabled for the TRV plugin are: {}'.format(message))

    def listActiveDebugging(self, monitorDebugTypes):            
        self.methodTracer.threaddebug(u'Main Plugin Method')

        loop = 0
        listedTypes = ''
        for monitorDebugType in monitorDebugTypes:
            if loop == 0:
                listedTypes = listedTypes + monitorDebugType
            else:
                listedTypes = listedTypes + ', ' + monitorDebugType
            loop += 1
        return listedTypes

    # def runConcurrentThread(self):

    #     self.secondCounter = int(indigo.server.getTime().time().second)

    #     try:
    #         while True:
    #             self.sleep(5)  # Sleep for 5 seconds

    #     except self.StopThread:
    #         pass    # Optionally catch the StopThread exception and do any needed cleanup.


    def zwaveWakeupMissedTriggered(self, trvCtlrDevId, devType, devId):

        try:
            self.methodTracer.threaddebug(u'Main Plugin Method')

            # Wakeup missed

            if devType == TRV:
                self.globals['trvc'][trvCtlrDevId]['zwaveWakeupDelayTrv'] = True 
                nextWakeupMissedSeconds = self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'] * 60
                deviceType = 'TRV device'
                lastWakeupTime = self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeTrv']
            else:  # Must be Remote
                self.globals['trvc'][trvCtlrDevId]['zwaveWakeupDelayRemote'] = True 
                nextWakeupMissedSeconds = self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalRemote'] * 60
                deviceType = 'Remote Thermostat device'
                lastWakeupTime = self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeRemote']

            indigo.devices[trvCtlrDevId].updateStateImageOnServer(indigo.kStateImageSel.TimerOn)

            self.generalLogger.error(u'Z-Wave wakeup missed for {} \'{}\', controlled by \'{}\'. Last Z-wave command received: {}'.format(deviceType, indigo.devices[devId].name, indigo.devices[trvCtlrDevId].name, lastWakeupTime))

            self.globals['timers']['zwaveWakeupCheck'][devId] = threading.Timer(float(nextWakeupMissedSeconds), self.zwaveWakeupMissedTriggered, [trvCtlrDevId, devType, devId])
            self.globals['timers']['zwaveWakeupCheck'][devId].setDaemon(True)
            self.globals['timers']['zwaveWakeupCheck'][devId].start()

        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [zwaveWakeupMissedTriggered]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def zwaveCommandQueued(self, zwaveCommand):  # Not yet available in Indigo API :)

        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.generalLogger.error(u'QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED')

    def zwaveCommandReceived(self, zwaveCommand):

        try:
            self.methodTracer.threaddebug(u'Main Plugin Method')

            self.currentTime = indigo.server.getTime()

            byteList = zwaveCommand['bytes']         # List of the raw bytes just received.
            byteListStr = convertListToHexStr(byteList)  # this method is defined in the example SDK plugin

            nodeId = zwaveCommand['nodeId']          # Can be None!
            endpoint = zwaveCommand['endpoint']      # Often will be None!

            if nodeId and nodeId in self.globals['zwave']['WatchList']:
                zwaveReportDetail = ''
                zwaveReport = u'\n\nZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ\nZZ'
                if nodeId and endpoint:
                   zwaveReport = zwaveReport + u"\nZZ  TRV Z-WAVE > RECEIVE INTERCEPTED: {} (node {:03d}, endpoint {})".format(byteListStr, nodeId, endpoint)
                elif nodeId:
                   zwaveReport = zwaveReport + u"\nZZ  TRV Z-WAVE > RECEIVE INTERCEPTED: {} (node {:03d})".format(byteListStr, nodeId)

                # 01 0A 00 13 49 03 40 01 01 25 73 BA
                # 0  3  6  9  12 15 18 21 24 27 30 33                      
                zwaveCommandNodeId = byteListStr[15:17]
                zwaveCommandLength = byteListStr[3:5]
                zwaveCommandClass = byteListStr[21:23]
                zwaveCommandVerb =  byteListStr[24:26]
                if zwaveCommandClass in ZWAVE_COMMAND_CLASS_TRANSLATION:
                    zwaveCommandClassUi = ZWAVE_COMMAND_CLASS_TRANSLATION[zwaveCommandClass]
                    if zwaveCommandVerb in ZWAVE_COMMAND_VERB_TRANSLATION:
                        zwaveCommandVerbUi = ZWAVE_COMMAND_VERB_TRANSLATION[zwaveCommandVerb]
                    else:
                        zwaveCommandVerbUi = 'Verb \'{}\' unknown to plugin'.format(zwaveCommandVerb)
                    address = int(zwaveCommandNodeId,16)
                    if address in self.globals['zwave']['addressToDevice']:
                        dev = indigo.devices[self.globals['zwave']['addressToDevice'][address]['devId']]  # TRV or Remote
                        devId = dev.id
                        devType = self.globals['zwave']['addressToDevice'][address]['type']
                        trvcDev = indigo.devices[self.globals['zwave']['addressToDevice'][address]['trvcId']]  # TRV Controller
                        trvCtlrDevId = trvcDev.id
                        if devType == TRV:
                            self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeTrv'] = self.currentTime.strftime('%Y-%m-%d %H:%M:%S')
                            if 'zwaveReceivedCountTrv' in self.globals['trvc'][trvCtlrDevId]:           
                                self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountTrv'] += 1
                            else:
                                self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountTrv'] = 1

                            self.globals['trvc'][trvCtlrDevId]['zwaveLastReceivedCommandTrv'] = zwaveCommandClass

                            if self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'] > 0:
                                if self.globals['trvc'][trvCtlrDevId]['zwaveWakeupDelayTrv']:
                                    self.globals['trvc'][trvCtlrDevId]['zwaveWakeupDelayTrv'] = False
                                    self.generalLogger.info(u'Z-Wave connection re-established with {} \'{}\', controlled by \'{}\'. This device had previously missed a wakeup.'.format('TRV device', indigo.devices[devId].name, indigo.devices[trvCtlrDevId].name))

                                    trvcDev.updateStateImageOnServer(indigo.kStateImageSel.HvacHeatMode)

                                nextWakeupMissedSeconds = (self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'] + 2) * 60  # Add 2 minutes to next expected wakeup
                                if devId in self.globals['timers']['zwaveWakeupCheck']:
                                    self.globals['timers']['zwaveWakeupCheck'][devId].cancel()
                                self.globals['timers']['zwaveWakeupCheck'][devId] = threading.Timer(float(nextWakeupMissedSeconds), self.zwaveWakeupMissedTriggered, [trvCtlrDevId, devType, devId])
                                self.globals['timers']['zwaveWakeupCheck'][devId].setDaemon(True)
                                self.globals['timers']['zwaveWakeupCheck'][devId].start()
                                zwaveReport = zwaveReport + u"\nZZ  TRV Z-WAVE > Next wakeup missed alert in {} seconds".format(nextWakeupMissedSeconds)

                        else:  # Must be Remote
                            self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeRemote'] = self.currentTime.strftime('%Y-%m-%d %H:%M:%S')
                            if 'zwaveReceivedCountRemote' in self.globals['trvc'][trvCtlrDevId]:            
                                self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountRemote'] += 1
                            else:
                                self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountRemote'] = 1
                            self.globals['trvc'][trvCtlrDevId]['zwaveLastReceivedCommandRemote'] = zwaveCommandClass

                            if self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalRemote'] > 0:
                                if self.globals['trvc'][trvCtlrDevId]['zwaveWakeupDelayRemote']:
                                    self.globals['trvc'][trvCtlrDevId]['zwaveWakeupDelayRemote'] = False
                                    self.generalLogger.info(u'Z-Wave connection re-established with {} \'{}\', controlled by \'{}\'. This device had previously missed a wakeup.'.format('Remote Thermostat device', indigo.devices[devId].name, indigo.devices[trvCtlrDevId].name))

                                    trvcDev.updateStateImageOnServer(indigo.kStateImageSel.HvacHeatMode)

                                nextWakeupMissedSeconds = (self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalRemote'] + 2) * 60  # Add 2 minutes to next expected wakeup
                                if devId in self.globals['timers']['zwaveWakeupCheck']:
                                    self.globals['timers']['zwaveWakeupCheck'][devId].cancel()
                                self.globals['timers']['zwaveWakeupCheck'][devId] = threading.Timer(float(nextWakeupMissedSeconds), self.zwaveWakeupMissedTriggered, [trvCtlrDevId, devType, devId])
                                self.globals['timers']['zwaveWakeupCheck'][devId].setDaemon(True)
                                self.globals['timers']['zwaveWakeupCheck'][devId].start()
                                zwaveReport = zwaveReport + u"\nZZ  TRV Z-WAVE > Next wakeup missed alert in {} seconds".format(nextWakeupMissedSeconds)

                        if zwaveCommandClass == ZWAVE_COMMAND_CLASS_THERMOSTAT_SETPOINT:

                            # 01 0C 00 04 00 66 06 43 03 01 42 01 F4 61

                            zwaveCommandSetpoint = float(float(int(int(byteListStr[33:35], 16) * 256) + int(byteListStr[36:38], 16)) / 10) # 01 0C 00 04 08 65 06 43 03 01 22 00 DC 23
                        #                                                                                              # 01 0C 00 04 00 63 06 43 03 01 22 00 82 73 
                            if zwaveCommandSetpoint > 30.0:
                                zwaveCommandSetpoint = float(zwaveCommandSetpoint / 10.0)
                            zwaveReportDetail = u', Setpoint = {}'.format(zwaveCommandSetpoint)

                            if devType == TRV and self.globals['trvc'][trvCtlrDevId]['trvSupportsManualSetpoint']:
                                self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_TRV_HARDWARE
                                zwaveReportDetail = zwaveReportDetail + u', Pending Controller Mode = {}'.format(CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_TRV_HARDWARE])
                            elif devType == REMOTE and self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl']:
                                self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_REMOTE_HARDWARE
                                zwaveReportDetail = zwaveReportDetail + u', Pending Controller Mode = {}'.format(CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_REMOTE_HARDWARE])

                        elif zwaveCommandClass == ZWAVE_COMMAND_CLASS_BATTERY:
                            zwaveCommandBattery = int(byteListStr[27:29], 16)  # 01 09 00 04 00 67 03 80 03 5D 48
                            zwaveReportDetail =  u', Battery = {}%'.format(zwaveCommandBattery)

                        elif zwaveCommandClass == ZWAVE_COMMAND_CLASS_SWITCH_MULTILEVEL:  # 01 09 00 04 00 63 03 26 03 50 E7
                            zwaveCommandValve = int(byteListStr[27:29], 16)  # 01 09 00 04 00 67 03 80 03 5D 48
                            if zwaveCommandValve == 0:
                                zwaveReportDetail = u', Valve = Closed'
                            else:
                                zwaveCommandValve = int(round(100.0 * (float(zwaveCommandValve) / 99.0)))
                                zwaveReportDetail = u', Valve = Open {}%'.format(zwaveCommandValve)

                        elif zwaveCommandClass == ZWAVE_COMMAND_CLASS_THERMOSTAT_MODE_CONTROL:  # 01 0A 00 04 00 63 04 40 03 0F 00 DA
                            zwaveCommandMode = int(byteListStr[27:29], 16)  # 01 09 00 04 00 67 03 80 03 5D 48
                            if zwaveCommandMode in HVAC_TRANSLATION:
                                zwaveCommandModeUi = HVAC_TRANSLATION[zwaveCommandMode]
                                zwaveReportDetail = u', Mode = {}'.format(zwaveCommandModeUi)
                            else:
                                zwaveReportDetail = u', Mode = {} unknown'.format(zwaveCommandMode)

                            # if devType == TRV:
                            #     self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_TRV_HARDWARE
                            # else:  # Must be Remote as can't be a valve
                            #     self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_REMOTE_HARDWARE

                        elif zwaveCommandClass == ZWAVE_COMMAND_CLASS_SENSOR_MULTILEVEL:
                            zwaveCommandTemperature = float(float(int(int(byteListStr[33:35], 16) * 256) + int(byteListStr[36:38], 16)) / 10.0) # 01 0C 00 04 00 63 06    31 05 01 42 08 97 7A

                            if zwaveCommandTemperature > 30.0:
                                zwaveCommandTemperature = float(zwaveCommandTemperature / 10.0)

                            # zwaveCommandTemperature = float(float(int(byteListStr[36:38], 16)) / 10)                        # 01 0C 00 04 08 69 06 31 05 01 22 00 E6 61
                            zwaveReportDetail = u', Temperature = {}'.format(zwaveCommandTemperature)

                        zwaveReport = zwaveReport + u'\nZZ  TRV Z-WAVE > TRANSLATION: Name = \'{}\', Address = {}, Length = {}, Class = {}, Verb = {}{}'.format(dev.name, address, int(zwaveCommandLength, 16), zwaveCommandClassUi, zwaveCommandVerbUi, zwaveReportDetail)

                        if zwaveCommandClass == ZWAVE_COMMAND_CLASS_WAKEUP:
                            if devType == TRV or devType == VALVE:
                                # As just a wakeup received - update TRV Controller device to ensure last TRV wakeup time recorded
                                trvcDev.updateStateOnServer(key='zwaveEventReceivedDateTimeTrv', value=self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeTrv'])
                            elif devType == REMOTE:
                                # As just a wakeup received - update TRV Controller device to ensure last Remote wakeup time recorded
                                trvcDev.updateStateOnServer(key='zwaveEventReceivedDateTimeRemote', value=self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeRemote'])
                            zwaveReport = zwaveReport + u'\nZZ' + self.globals['trvc'][trvCtlrDevId]['zwaveEventWakeUpSentDisplayFix']
                            self.globals['trvc'][trvCtlrDevId]['zwaveEventWakeUpSentDisplayFix'] = ''

                    else: 
                        zwaveReport = zwaveReport + u'\nZZ  TRV Z-WAVE > TRANSLATION: Address = {}, Length = {}, Class = {}, Verb = {}'.format(address, int(zwaveCommandLength, 16), zwaveCommandClassUi, zwaveCommmandVerbUi)

                else:
                    zwaveReport = zwaveReport + u'\nZZ TRV Z-WAVE > TRANSLATION: \'0x{}\' Class unknown'.format(zwaveCommandClass)

                zwaveReport = zwaveReport + u'\nZZ\nZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ\n\n'

                self.generalLogger.debug(zwaveReport)

        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [zwaveCommandReceived]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def zwaveCommandSent(self, zwaveCommand):

        try:
            self.methodTracer.threaddebug(u'Main Plugin Method')

            byteList = zwaveCommand['bytes']         # List of the raw bytes just sent.
            byteListStr = convertListToHexStr(byteList)    # this method is defined in the example SDK plugin
            timeDelta = zwaveCommand['timeDelta']    # The time duration it took to receive an Z-Wave ACK for the command.
            zwaveCommandSuccess = zwaveCommand['cmdSuccess']  # True if an ACK was received (or no ACK expected), false if NAK.
            nodeId = zwaveCommand['nodeId']          # Can be None!
            endpoint = zwaveCommand['endpoint']      # Often will be None!

            if nodeId and nodeId in self.globals['zwave']['WatchList']:
                zwaveReport = u'\n\nZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ\nZZ'
                if zwaveCommandSuccess:
                    sendInterception = u'\nZZ  TRV Z-WAVE > SEND INTERCEPTED: {} (node {:03d} ACK after {} milliseconds)'.format(byteListStr, nodeId, timeDelta)
                else:
                    sendInterception = u'\nZZ  TRV Z-WAVE > SEND INTERCEPTED: {} (failed)'.format(byteListStr)
                zwaveReport = zwaveReport + sendInterception     
                zwaveCommandNodeId = byteListStr[12:14]
                zwaveCommandLength = byteListStr[3:5]
                zwaveCommandClass = byteListStr[18:20]
                zwaveCommandVerb =  byteListStr[21:23]
                zwaveCommandSubclass =  byteListStr[21:23]
                trvCtlrDevId = 0
                if zwaveCommandClass in ZWAVE_COMMAND_CLASS_TRANSLATION:
                    zwaveCommandClassUi = ZWAVE_COMMAND_CLASS_TRANSLATION[zwaveCommandClass]
                    if zwaveCommandVerb in ZWAVE_COMMAND_VERB_TRANSLATION:
                        zwaveCommandVerbUi = ZWAVE_COMMAND_VERB_TRANSLATION[zwaveCommandVerb]
                    else:
                        zwaveCommandVerbUi = 'Verb \'{}\' unknown to plugin'.format(zwaveCommandVerb)
                    address = int(zwaveCommandNodeId,16)
                    if address in self.globals['zwave']['addressToDevice']:
                        dev = indigo.devices[self.globals['zwave']['addressToDevice'][address]['devId']]
                        devId = dev.id
                        devType = self.globals['zwave']['addressToDevice'][address]['type']
                        trvcDev = indigo.devices[self.globals['zwave']['addressToDevice'][address]['trvcId']]  # TRV Controller
                        trvCtlrDevId = trvcDev.id
                        self.globals['trvc'][trvCtlrDevId]['zwaveEventWakeUpSentDisplayFix'] = '' 
                        sendTranslation = u'\nZZ  TRV Z-WAVE > TRANSLATION: Name = \'{}\', Address = {}, Length = {}, Class = {}, Verb = {}'.format(dev.name, address, int(zwaveCommandLength,16), zwaveCommandClassUi, zwaveCommandVerbUi)
                        zwaveReport = zwaveReport + sendTranslation
                        if devType == TRV or devType == VALVE:
                            self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeTrv'] = self.currentTime.strftime('%Y-%m-%d %H:%M:%S')            
                            if 'zwaveSentCountTrv' in self.globals['trvc'][trvCtlrDevId]:
                                self.globals['trvc'][trvCtlrDevId]['zwaveSentCountTrv'] += 1
                            else:
                                self.globals['trvc'][trvCtlrDevId]['zwaveSentCountTrv'] = 1

                            self.globals['trvc'][trvCtlrDevId]['zwaveLastSentCommandTrv'] = zwaveCommandClass
                        else:  # Must be Remote
                            self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeRemote'] = self.currentTime.strftime('%Y-%m-%d %H:%M:%S')
                            if 'zwaveSentCountRemote' in self.globals['trvc'][trvCtlrDevId]:            
                                self.globals['trvc'][trvCtlrDevId]['zwaveSentCountRemote'] += 1
                            else:
                                self.globals['trvc'][trvCtlrDevId]['zwaveSentCountRemote'] = 1
                            self.globals['trvc'][trvCtlrDevId]['zwaveLastSentCommandRemote'] = zwaveCommandClass
                    else:
                        sendTranslation = u'\nZZ  TRV Z-WAVE > TRANSLATION: Address = {}, Length = {}, Class = {}, Verb = {}'.format(address, int(zwaveCommandLength,16), zwaveCommandClassUi, zwaveCommandVerbUi)
                        zwaveReport = zwaveReport + sendTranslation

                    if zwaveCommandClass == ZWAVE_COMMAND_CLASS_THERMOSTAT_SETPOINT:  # 01 0C 00 13 63 05 43 01 01 01 08 25 27 CE
                        zwaveCommandSetpoint = float(int(byteListStr[30:32], 16))
                        zwaveReport = zwaveReport + u', Setpoint = {}'.format(zwaveCommandSetpoint)

                        if devType == TRV:
                            zwaveReport = zwaveReport + u'\nZZ  \'TRV\' device Z-WAVE > Pending: {}, Sequence:  \'{}\', Setpoint: \'{}\''.format(self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointFlag'], self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointSequence'], self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointValue'])


                            if self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointValue'] != zwaveCommandSetpoint:  # Assume  internally generated Z-Wave setpoint command

                            # if self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointFlag']:  # if internally generated Z-Wave setpoint command reset flag

                            #     self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointFlag'] = False  # Turn off 
                            # else:
                                # As not internally generated Z-Wave setpoint command, must be from UI
                                self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_TRV_UI

                        else:  # Must be Remote as can't be a valve
                            zwaveReport = zwaveReport + u'\nZZ  Remote Z-WAVE > Pending: {}, Sequence:  \'{}\', Setpoint: \'{}\''.format(self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointFlag'], self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointSequence'], self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointValue'])
                            if self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointFlag']:  # if internally generated Z-Wave setpoint command reset flag
                                self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointFlag'] = False  # Turn off 
                            else:
                                # As not internally generated Z-Wave setpoint command, must be from UI
                                self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_REMOTE_UI


                    elif zwaveCommandClass == ZWAVE_COMMAND_CLASS_SWITCH_MULTILEVEL:  # 01 0A 00 13 63 03 26 01 63 25 BF 58
                        #                                                             # 01 09 00 13 63 02 26 02 25 C3 46
                        if zwaveCommandSubclass == ZWAVE_COMMAND_SUBCLASS_STATUS:
                            zwaveReport = zwaveReport + u', {}'.format(ZWAVE_COMMAND_SUBCLASS_TRANSLATION[zwaveCommandSubclass])
                        elif zwaveCommandSubclass == ZWAVE_COMMAND_SUBCLASS_SET:
                            zwaveCommandValve = int(byteListStr[24:26], 16)
                            if zwaveCommandValve == 0:
                                zwaveReport = zwaveReport + u', {} Closed'.format(ZWAVE_COMMAND_SUBCLASS_TRANSLATION[zwaveCommandSubclass])
                            else:
                                if zwaveCommandValve == 99:
                                    zwaveCommandValve = 100
                                zwaveReport = zwaveReport + u', {} Open {}%'.format(ZWAVE_COMMAND_SUBCLASS_TRANSLATION[zwaveCommandSubclass], zwaveCommandValve)

                    elif zwaveCommandClass == ZWAVE_COMMAND_CLASS_THERMOSTAT_MODE_CONTROL:  # 01 0A 00 13 63 03 40 01 00 25 BD 5F
                        zwaveCommandMode = int(byteListStr[24:26], 16)  # 01 09 00 04 00 67 03 80 03 5D 48
                        if zwaveCommandMode in HVAC_TRANSLATION:
                            zwaveCommandModeUi = HVAC_TRANSLATION[zwaveCommandMode]
                            zwaveReport = zwaveReport + u', Mode = {}'.format(zwaveCommandModeUi)
                        else:
                            zwaveReport = zwaveReport + u', Mode = {} unknown'.format(zwaveCommandMode)

                        if self.globals['trvc'][trvCtlrDevId]['zwavePendingHvac']:  # if internally generated Z-Wave hvac command reset flag
                            self.globals['trvc'][trvCtlrDevId]['zwavePendingHvac'] = False  # Turn off 
                        else:
                            pass
                            # As not internally generated Z-Wave hvac command, must be from UI
                            # if devType == TRV:
                            #     self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_TRV_UI
                            # else:  # Must be Remote as can't be a valve
                            #     self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_REMOTE_UI

                    elif zwaveCommandClass == ZWAVE_COMMAND_CLASS_WAKEUP:
                        self.globals['trvc'][trvCtlrDevId]['zwaveEventWakeUpSentDisplayFix'] = sendInterception + sendTranslation

                else:
                    zwaveReport = zwaveReport + u'\nZZ  TRV Z-WAVE > TRANSLATION: \'{}\' Class unknown'.format(zwaveCommandClass)

                zwaveReport = zwaveReport + u'\nZZ\nZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ\n\n'

                if trvCtlrDevId != 0 and len(self.globals['trvc'][trvCtlrDevId]['zwaveEventWakeUpSentDisplayFix']) == 0:  # Not a Wakeup command - so output Z-Wave report
                    self.generalLogger.debug(zwaveReport)

        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [zwaveCommandSent]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

        # sent "Spirit - TRV [Testing 1]" mode change to heat
        # sent: 01 0A 00 13 63 03 40 01 01 25 9F 7C (node 099 ACK after 1332 milliseconds)

        # sent "Spirit - TRV [Testing 1]" mode change to off
        # sent: 01 0A 00 13 63 03 40 01 00 25 A1 43 (node 099 ACK after 1336 milliseconds)

        # sent "Spirit - TRV [Testing 1]" increase heat setpoint to 18.0°
        # sent: 01 0C 00 13 63 05 43 01 01 01 12 25 B9 4A (node 099 ACK after 1291 milliseconds)

        # sent "Spirit - TRV [Testing 1]" increase heat setpoint to 18.0°
        # sent: 01 0C 00 13 63 05 43 01 01 01 12 25 BA 49 (node 099 ACK after 33 milliseconds)

        # sent "Spirit - TRV [Testing 1]" increase heat setpoint to 19.0°
        # sent: 01 0C 00 13 63 05 43 01 01 01 13 25 C4 36 (node 099 ACK after 1280 milliseconds)

        # sent "Spirit - TRV [Testing 1]" increase heat setpoint to 19.0°
        # sent: 01 0C 00 13 63 05 43 01 01 01 13 25 C5 37 (node 099 ACK after 33 milliseconds)

        # sent "Spirit - TRV [Testing 1]" increase heat setpoint to 20.0°
        # sent: 01 0C 00 13 63 05 43 01 01 01 14 25 C6 33 (node 099 ACK after 32 milliseconds)

        # sent "Spirit - TRV [Testing 1]" increase heat setpoint to 21.0°
        # sent: 01 0C 00 13 63 05 43 01 01 01 15 25 C7 33 (node 099 ACK after 33 milliseconds)

        # sent: 01 09 00 13 69 02 84 08 24 42 64 (node 105 ACK after 0 milliseconds)


    def deviceRaceConditionReEnableTriggered(self, trvCtlrDevId):

        try:
            self.methodTracer.threaddebug(u'Main Plugin Method')

            if trvCtlrDevId in self.globals['timers']['raceCondition']:                            
                self.globals['timers']['raceCondition'][trvCtlrDevId].cancel()

            self.generalLogger.error(u'Re-Enabling TRV Controller \'{}\' following potential race condition detection (which as a result the device was disabled).'.format(indigo.devices[trvCtlrDevId].name))   
            indigo.device.enable(trvCtlrDevId, value=True)

        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [deviceRaceConditionReEnableTriggered]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def deviceUpdated(self, origDev, newDev):

        self.methodTracer.threaddebug(u'Main Plugin Method')


        def secondsSinceMidnight():
            utcnow = datetime.datetime.utcnow()
            midnight_utc = datetime.datetime.combine(utcnow.date(), datetime.time(0))
            delta = utcnow - midnight_utc
            return int(delta.seconds)

        try:
            if newDev.deviceTypeId == 'trvController' and newDev.configured and newDev.id in self.globals['trvc'] and self.globals['trvc'][newDev.id]['deviceStarted']:  # IGNORE THESE UPDATES TO AVOID LOOP!!!


                trvCtlrDevId = newDev.id

                seconds = secondsSinceMidnight()
                if self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updateSecondsSinceMidnight'] != seconds:
                    self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updateSecondsSinceMidnight'] = seconds
                    self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updatesInLastSecond'] = 1
                    self.generalLogger.debug(u'=======> RACE DETECTION FOR TRV CONTROLLER \'{}\': SECONDS SINCE MIDNIGHT = \'{}\', COUNT RESET TO 1'.format(newDev.name, seconds))   
                else:
                    self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updatesInLastSecond'] += 1
                    if self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updatesInLastSecond'] > self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updatesInLastSecondMaximum']:
                        self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updatesInLastSecondMaximum'] = self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updatesInLastSecond']
                    self.generalLogger.debug(u'=======> RACE DETECTION FOR TRV CONTROLLER \'{}\': SECONDS SINCE MIDNIGHT = \'{}\', COUNT = \'{}\' [MAX = \'{}\'] <======='.format(newDev.name, seconds, self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updatesInLastSecond'], self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updatesInLastSecondMaximum']))   
                    if self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updatesInLastSecond'] > RACE_CONDITION_LIMIT:
                        self.generalLogger.error(u'Potential race condition detected for TRV Controller \'{}\' in TRV Plugin [deviceUpdated] - TRV Controller device being disabled!'.format(newDev.name))   
                        indigo.device.enable(trvCtlrDevId, value=False)

                        # setting a timer to re-enable after 60 seconds

                        self.globals['timers']['raceCondition'][trvCtlrDevId] = threading.Timer(60.0, self.deviceRaceConditionReEnableTriggered, [trvCtlrDevId])
                        self.globals['timers']['raceCondition'][trvCtlrDevId].setDaemon(True)
                        self.globals['timers']['raceCondition'][trvCtlrDevId].start()

                        return  # Note that the 'finally:' staement at the end of this method will retun the correct values to Indigo

                deviceUpdatedLog = u'\n\n======================================================================================================================================================\n=='
                deviceUpdatedLog = deviceUpdatedLog + u'\n==  Method: \'deviceUpdated\''
                self.globals['deviceUpdatedSequenceCount'] += 1
                deviceUpdatedLog = deviceUpdatedLog + u'\n==  Sequence: {}'.format(self.globals['deviceUpdatedSequenceCount'])
                deviceUpdatedLog = deviceUpdatedLog + u'\n==  Device: TRV CONTROLLER - \'{}\''.format(newDev.name)
                deviceUpdatedLog = deviceUpdatedLog + u'\n==  Last Communication: {}'.format(newDev.lastSuccessfulComm)


                self.globals['trvc'][trvCtlrDevId]['lastSuccessfulComm'] = newDev.lastSuccessfulComm

                updateRequested = False

                updateList = dict()
                updateLog = dict()

                if origDev.hvacMode != newDev.hvacMode:
                    oldInternalHvacMode = self.globals['trvc'][trvCtlrDevId]['hvacOperationMode']
                    self.globals['trvc'][trvCtlrDevId]['hvacOperationMode'] = newDev.hvacMode
                    updateRequested = True
                    updateLog[UPDATE_CONTROLLER_HVAC_OPERATION_MODE] = u'TRV Controller HVAC Operation Mode updated from {} to {} [Internal store was = {} and is now = {}]'.format(HVAC_TRANSLATION[origDev.hvacMode], HVAC_TRANSLATION[newDev.hvacMode], HVAC_TRANSLATION[oldInternalHvacMode], HVAC_TRANSLATION[int(self.globals['trvc'][trvCtlrDevId]['hvacOperationMode'])])


                # if (float(origDev.temperatures[0]) != float(newDev.temperatures[0])) or (self.globals['trvc'][trvCtlrDevId]['temperature'] != float(newDev.temperatures[0])):
                #     origTemp = float(origDev.temperatures[0])
                #     newTemp = float(newDev.temperatures[0])
                #     updateRequested = True
                #     updateList[UPDATE_CONTROLLER_TEMPERATURE] = newTemp
                #     updateLog[UPDATE_CONTROLLER_TEMPERATURE] = u'Temperature updated from {} to {} [Internal store = {}]'.format(origTemp, newTemp, self.globals['trvc'][trvCtlrDevId]['temperature'])

                # self.generalLogger.debug(u'\'{}\' [TRV CONTROLLER] Heat Setpoint potentially changed from {} to {} [Internal store = {}]'.format(newDev.name, origDev.heatSetpoint, newDev.heatSetpoint, self.globals['trvc'][trvCtlrDevId]['setpointHeat']))

                if origDev.states['controllerMode'] != newDev.states['controllerMode']:
                    oldInternalControllerMode = self.globals['trvc'][trvCtlrDevId]['controllerMode']
                    self.globals['trvc'][trvCtlrDevId]['controllerMode'] = newDev.states['controllerMode']
                    updateRequested = True
                    updateLog[UPDATE_CONTROLLER_MODE] = u'Controller Mode updated from {} to {} [Internal store was = {} and is now = {}]'.format(CONTROLLER_MODE_TRANSLATION[origDev.states['controllerMode']], CONTROLLER_MODE_TRANSLATION[newDev.states['controllerMode']], CONTROLLER_MODE_TRANSLATION[oldInternalControllerMode], CONTROLLER_MODE_TRANSLATION[self.globals['trvc'][trvCtlrDevId]['controllerMode']])

                if float(origDev.heatSetpoint) != float(newDev.heatSetpoint):
                    oldInternalSetpointHeat = self.globals['trvc'][trvCtlrDevId]['setpointHeat']
                    self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(newDev.heatSetpoint)
                    updateRequested = True
                    updateLog[UPDATE_CONTROLLER_HEAT_SETPOINT] = u'TRV Controller Heat Setpoint changed from {} to {} [Internal was = {} and is now = {}]'.format(origDev.heatSetpoint, newDev.heatSetpoint, oldInternalSetpointHeat, self.globals['trvc'][trvCtlrDevId]['setpointHeat'])

                    if self.globals['trvc'][trvCtlrDevId]['updateCsvFile']:
                        if self.globals['trvc'][trvCtlrDevId]['updateAllCsvFiles']:
                            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES, trvCtlrDevId, None])
                        else:
                            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['setpointHeat', self.globals['trvc'][trvCtlrDevId]['setpointHeat']]])

                if updateRequested:

                    if len(updateList) > 0:

                        deviceUpdatedLog = deviceUpdatedLog + '\n==  List of states to be queued for update by TRVHANDLER:'.format(newDev.name)
                        deviceUpdatedLog = deviceUpdatedLog + '\n==  List of states that have changed for TRVHANDLER [controlTrv]:'.format(newDev.name)
                        for itemToUpdate in updateList.iteritems():
                            updateKey = itemToUpdate[0]
                            updateValue = itemToUpdate[1]
                            deviceUpdatedLog = deviceUpdatedLog + '\n==    > Description = {}, Value = {}'.format(UPDATE_TRANSLATION[updateKey], updateValue)
                    
                        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_UPDATE_TRV_CONTROLLER_STATES, trvCtlrDevId, [updateList, ]])

                    deviceUpdatedLog = deviceUpdatedLog + '\n==  Description of states that have changed for TRVHANDLER [controlTrv]:'.format(newDev.name)
                    for itemToUpdate in updateLog.iteritems():
                        updateKey = itemToUpdate[0]
                        updateValue = itemToUpdate[1]
                        deviceUpdatedLog = deviceUpdatedLog + '\n==    > {}'.format(updateValue)

                    self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_CONTROL_TRV, trvCtlrDevId, None])

                    deviceUpdatedLog = deviceUpdatedLog + u'\n==\n======================================================================================================================================================\n\n'
                else:
                    deviceUpdatedLog = deviceUpdatedLog + u'\n==\n== No updates to TRV Controller that are of interest to the plugin'
                    deviceUpdatedLog = deviceUpdatedLog + self.deviceUpdatedList(origDev, newDev, '==')

                    deviceUpdatedLog = deviceUpdatedLog + u'\n==\n======================================================================================================================================================\n\n'
                    deviceUpdatedLog = ''
    
                if len(deviceUpdatedLog) > 0:
                    self.generalLogger.debug(deviceUpdatedLog)

            elif int(newDev.id) in self.globals['devicesToTrvControllerTable'].keys():  # Check if a TRV or Remote Thermostat already stored in table

                deviceUpdatedLog = u'\n\n======================================================================================================================================================\n=='
                deviceUpdatedLog = deviceUpdatedLog + u'\n==  Method: \'deviceUpdated\''
                self.globals['deviceUpdatedSequenceCount'] += 1
                deviceUpdatedLog = deviceUpdatedLog + u'\n==  Sequence: {}'.format(self.globals['deviceUpdatedSequenceCount'])
                deviceUpdatedLog = deviceUpdatedLog + u'\n==  Device: {} - \'{}\''.format(DEVICE_TYPE_TRANSLATION[self.globals['devicesToTrvControllerTable'][newDev.id]['type']], newDev.name)
                deviceUpdatedLog = deviceUpdatedLog + u'\n==  Last Communication: {}'.format(newDev.lastSuccessfulComm)

                trvCtlrDevId = int(self.globals['devicesToTrvControllerTable'][newDev.id]['trvControllerId'])

                if indigo.devices[trvCtlrDevId].enabled:

                    trvControllerDev = indigo.devices[trvCtlrDevId]

                    updateRequested = False

                    updateList = dict()

                    updateLog = dict()

                    if self.globals['devicesToTrvControllerTable'][newDev.id]['type'] == TRV or self.globals['devicesToTrvControllerTable'][newDev.id]['type'] == VALVE:

                        seconds = secondsSinceMidnight()
                        if self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updateSecondsSinceMidnight'] != seconds:
                            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updateSecondsSinceMidnight'] = seconds
                            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updatesInLastSecond'] = 1
                            self.generalLogger.debug(u'=======> RACE DETECTION FOR TRV \'{}\': SECONDS SINCE MIDNIGHT = \'{}\', COUNT RESET TO 1'.format(newDev.name, seconds))   
                        else:
                            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updatesInLastSecond'] += 1
                            if self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updatesInLastSecond'] > self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updatesInLastSecondMaximum']:
                                self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updatesInLastSecondMaximum'] = self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updatesInLastSecond']
                            self.generalLogger.debug(u'=======> RACE DETECTION FOR TRV \'{}\': SECONDS SINCE MIDNIGHT = \'{}\', COUNT = \'{}\' [MAX = \'{}\'] <======='.format(newDev.name, seconds, self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updatesInLastSecond'], self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updatesInLastSecondMaximum']))   
                            if self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updatesInLastSecond'] > RACE_CONDITION_LIMIT:
                                self.generalLogger.error(u'Potential race condition detected for TRV device managed by TRV Controller \'{}\' in TRV Plugin [deviceUpdated] - TRV Controller device being disabled!'.format(trvControllerDev.name))   
                                indigo.device.enable(trvCtlrDevId, value=False)

                                # setting a timer to re-enable after 60 seconds

                                self.globals['timers']['raceCondition'][trvCtlrDevId] = threading.Timer(60.0, self.deviceRaceConditionReEnableTriggered, [trvCtlrDevId])
                                self.globals['timers']['raceCondition'][trvCtlrDevId].setDaemon(True)
                                self.globals['timers']['raceCondition'][trvCtlrDevId].start()

                                return  # Note that the 'finally:' staement at the end of this method will retun the coreect values to Indigo

                        # The first checks are general across all sub-devices i.e thermostat and valve

                        self.globals['trvc'][trvCtlrDevId]['lastSuccessfulCommTrv'] = newDev.lastSuccessfulComm

                        # Check if Z-Wave Event has been received 
                        if self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountTrv'] > self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountPreviousTrv']:
                            self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountPreviousTrv'] = self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountTrv']
                            updateRequested = True
                            updateList[UPDATE_ZWAVE_EVENT_RECEIVED_TRV] = self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeTrv']
                            updateLog[UPDATE_ZWAVE_EVENT_RECEIVED_TRV] = u'TRV Z-Wave event received. Time updated to \'{}\'. Received count now totals: {}'.format(self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeTrv'], self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountTrv'])

                        # Check if Z-Wave Event has been sent 
                        if self.globals['trvc'][trvCtlrDevId]['zwaveSentCountTrv'] > self.globals['trvc'][trvCtlrDevId]['zwaveSentCountPreviousTrv']:
                            self.globals['trvc'][trvCtlrDevId]['zwaveSentCountPreviousTrv'] = self.globals['trvc'][trvCtlrDevId]['zwaveSentCountTrv']
                            updateRequested = True
                            updateList[UPDATE_ZWAVE_EVENT_SENT_TRV] = self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeTrv']
                            updateLog[UPDATE_ZWAVE_EVENT_SENT_TRV] = u'TRV Z-Wave event sent. Time updated to \'{}\'. Sent count now totals: {}'.format(self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeTrv'], self.globals['trvc'][trvCtlrDevId]['zwaveSentCountTrv'])

                        # Check the wakeup interval in case it has changed
                        self.wakeupInterval = int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].globalProps["com.perceptiveautomation.indigoplugin.zwave"]["zwWakeInterval"])
                        if int(self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv']) != self.wakeupInterval:
                            self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'] = self.wakeupInterval
                            updateRequested = True
                            updateList[UPDATE_ZWAVE_WAKEUP_INTERVAL] = self.wakeupInterval
                            updateLog[UPDATE_ZWAVE_WAKEUP_INTERVAL] = u'TRV Z-Wave wakeup interval changed from \'{}\' to \'{}\''.format(self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'], self.wakeupInterval)

                        # if newDev.globalProps['com.perceptiveautomation.indigoplugin.zwave']['zwDevSubIndex'] == 0:  # Thermostat
                        if self.globals['devicesToTrvControllerTable'][newDev.id]['type'] == TRV:

                            if trvControllerDev.states['controllerMode'] != self.globals['trvc'][trvCtlrDevId]['controllerMode']:
                                    updateRequested = True
                                    updateList[UPDATE_CONTROLLER_MODE] = self.globals['trvc'][trvCtlrDevId]['controllerMode']
                                    updateLog[UPDATE_CONTROLLER_MODE] = u'Controller Mode updated from {} to {}'.format(CONTROLLER_MODE_TRANSLATION[trvControllerDev.states['controllerMode']], CONTROLLER_MODE_TRANSLATION[self.globals['trvc'][trvCtlrDevId]['controllerMode']])

                            if 'batteryLevel' in newDev.states:
                                # self.generalLogger.debug(u'=====================>>>> Battery Level for TRV device \'{}\' - OLD: {}, NEW: {}'.format(origDev.name, origDev.batteryLevel, newDev.batteryLevel))
                                if (origDev.batteryLevel != newDev.batteryLevel) or (self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'] != newDev.batteryLevel):
                                    self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'] = newDev.batteryLevel
                                    updateRequested = True
                                    updateList[UPDATE_TRV_BATTERY_LEVEL] = newDev.batteryLevel
                                    updateLog[UPDATE_TRV_BATTERY_LEVEL] = u'TRV Battery Level updated from {} to {} [Internal store = \'{}\']'.format(origDev.batteryLevel, newDev.batteryLevel, self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'])

                            if self.globals['trvc'][trvCtlrDevId]['trvSupportsTemperatureReporting']:
                                if (float(origDev.temperatures[0]) != float(newDev.temperatures[0])) or (self.globals['trvc'][trvCtlrDevId]['temperatureTrv'] != float(newDev.temperatures[0])):
                                    origTemp = float(origDev.temperatures[0])
                                    newTemp = float(newDev.temperatures[0])
                                    updateRequested = True
                                    updateList[UPDATE_TRV_TEMPERATURE] = newTemp
                                    updateLog[UPDATE_TRV_TEMPERATURE] = u'Temperature updated from {} to {} [Internal store = \'{}\']'.format(origTemp, newTemp, self.globals['trvc'][trvCtlrDevId]['temperatureTrv'])

                                    if self.globals['trvc'][trvCtlrDevId]['updateCsvFile']:
                                        if self.globals['trvc'][trvCtlrDevId]['updateAllCsvFiles']:
                                            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES, trvCtlrDevId, None])
                                        else:
                                            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['temperatureTrv', newTemp]])


                            if (int(origDev.hvacMode) != int(newDev.hvacMode)) or (int(self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv']) != int(newDev.hvacMode)):

                                hvacMode = newDev.hvacMode
                                if hvacMode == HVAC_COOL or hvacMode == HVAC_AUTO:  # Don't allow HVAC Mode of Cool or Auto
                                    hvacMode = RESET_TO_HVAC_HEAT
                                    
                                updateRequested = True
                                updateList[UPDATE_TRV_HVAC_OPERATION_MODE] = hvacMode
                                if newDev.hvacMode == hvacMode:
                                    updateLog[UPDATE_TRV_HVAC_OPERATION_MODE] = u'TRV HVAC Operation Mode updated from \'{}\' to \'{}\' [Internal store = \'{}\']'.format(HVAC_TRANSLATION[origDev.hvacMode], HVAC_TRANSLATION[newDev.hvacMode], HVAC_TRANSLATION[int(self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'])])
                                else:
                                    updateLog[UPDATE_TRV_HVAC_OPERATION_MODE] = u'TRV HVAC Operation Mode update from \'{}\' to \'{}\', overridden and reset to \'{}\' [Internal store = \'{}\']'.format(HVAC_TRANSLATION[origDev.hvacMode], HVAC_TRANSLATION[newDev.hvacMode], HVAC_TRANSLATION[hvacMode], HVAC_TRANSLATION[self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv']])

                            if newDev.model == 'Thermostat (Spirit)':
                                if 'zwaveHvacOperationModeID' in newDev.states:
                                    if origDev.states['zwaveHvacOperationModeID'] != newDev.states['zwaveHvacOperationModeID']:

                                        zwaveHvacOperationModeID = newDev.states['zwaveHvacOperationModeID']
                                        if zwaveHvacOperationModeID == HVAC_COOL:  # Don't allow Cool
                                            zwaveHvacOperationModeID = RESET_TO_HVAC_HEAT
                                        elif zwaveHvacOperationModeID == HVAC_AUTO:  # Don't allow Auto
                                            zwaveHvacOperationModeID = RESET_TO_HVAC_HEAT
                                        updateRequested = True
                                        updateList[UPDATE_ZWAVE_HVAC_OPERATION_MODE_ID] = zwaveHvacOperationModeID
                                        if newDev.states['zwaveHvacOperationModeID'] == zwaveHvacOperationModeID:
                                            updateLog[UPDATE_ZWAVE_HVAC_OPERATION_MODE_ID] = u'ZWave HVAC Operation Mode updated from \'{}\' to \'{}\''.format(HVAC_TRANSLATION[origDev.states['zwaveHvacOperationModeID']], HVAC_TRANSLATION[newDev.states['zwaveHvacOperationModeID']])
                                        else:
                                            updateLog[UPDATE_ZWAVE_HVAC_OPERATION_MODE_ID] = u'ZWave HVAC Operation Mode update from \'{}\' to \'{}\', overridden and reset to \'{}\''.format(HVAC_TRANSLATION[origDev.states['zwaveHvacOperationModeID']], HVAC_TRANSLATION[newDev.states['zwaveHvacOperationModeID']], HVAC_TRANSLATION[zwaveHvacOperationModeID])

                            # if self.globals['trvc'][trvCtlrDevId]['trvSupportsManualSetpoint']:
                            #     if (float(origDev.heatSetpoint) != float(newDev.heatSetpoint)):
                            #         updateRequested = True
                            #         if self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_TRV_HARDWARE:
                            #             updateList[UPDATE_TRV_HEAT_SETPOINT_FROM_DEVICE] = newDev.heatSetpoint
                            #             updateLog[UPDATE_TRV_HEAT_SETPOINT_FROM_DEVICE] = u'TRV Heat Setpoint changed on device from {} to {} [Internal store = {}]'.format(origDev.heatSetpoint, newDev.heatSetpoint, self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])
                            #         else:
                            #             updateList[UPDATE_TRV_HEAT_SETPOINT] = newDev.heatSetpoint
                            #             updateLog[UPDATE_TRV_HEAT_SETPOINT] = u'TRV Heat Setpoint changed from {} to {} [Internal store = {}]'.format(origDev.heatSetpoint, newDev.heatSetpoint, self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])

                            # if self.globals['trvc'][trvCtlrDevId]['trvSupportsManualSetpoint']:
                            if (float(origDev.heatSetpoint) != float(newDev.heatSetpoint)):
                                updateRequested = True
                                if self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_TRV_HARDWARE:
                                    updateList[UPDATE_TRV_HEAT_SETPOINT_FROM_DEVICE] = newDev.heatSetpoint
                                    updateLog[UPDATE_TRV_HEAT_SETPOINT_FROM_DEVICE] = u'TRV Heat Setpoint changed on device from {} to {} [Internal store = {}]'.format(origDev.heatSetpoint, newDev.heatSetpoint, self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])
                                else:
                                    updateList[UPDATE_TRV_HEAT_SETPOINT] = newDev.heatSetpoint
                                    updateLog[UPDATE_TRV_HEAT_SETPOINT] = u'TRV Heat Setpoint changed from {} to {} [Internal store = {}]'.format(origDev.heatSetpoint, newDev.heatSetpoint, self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])

                                if self.globals['trvc'][trvCtlrDevId]['updateCsvFile']:
                                    if self.globals['trvc'][trvCtlrDevId]['updateAllCsvFiles']:
                                        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES, trvCtlrDevId, None])
                                    else:
                                        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['setpointHeatTrv', newDev.heatSetpoint]])

                        # elif newDev.globalProps['com.perceptiveautomation.indigoplugin.zwave']['zwDevSubIndex'] == 1:  # Valve ?
                        elif self.globals['devicesToTrvControllerTable'][newDev.id]['type'] == VALVE:
                            if newDev.model == 'Thermostat (Spirit)':  # Check to make sure it is a valve
                                if int(origDev.brightness) != int(newDev.brightness) or int(self.globals['trvc'][trvCtlrDevId]['valvePercentageOpen']) != int(newDev.brightness):
                                    updateRequested = True
                                    updateList[UPDATE_CONTROLLER_VALVE_PERCENTAGE] = int(newDev.brightness)
                                    updateLog[UPDATE_ZWAVE_HVAC_OPERATION_MODE_ID] = u'Valve Percentage Open updated from \'{}\' to \'{}\' [Internal store = {}]'.format(origDev.brightness, newDev.brightness, self.globals['trvc'][trvCtlrDevId]['valvePercentageOpen'])
                                    if self.globals['trvc'][trvCtlrDevId]['updateCsvFile']:
                                        if self.globals['trvc'][trvCtlrDevId]['updateAllCsvFiles']:
                                            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES, trvCtlrDevId, None])
                                        else:
                                            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['valvePercentageOpen', int(newDev.brightness)]])

                    elif self.globals['devicesToTrvControllerTable'][newDev.id]['type'] == REMOTE:

                        seconds = secondsSinceMidnight()
                        if self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updateSecondsSinceMidnight'] != seconds:
                            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updateSecondsSinceMidnight'] = seconds
                            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updatesInLastSecond'] = 1
                            self.generalLogger.debug(u'=======> RACE DETECTION FOR REMOTE \'{}\': SECONDS SINCE MIDNIGHT = \'{}\', COUNT RESET TO 1'.format(newDev.name, seconds))   
                        else:
                            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updatesInLastSecond'] += 1
                            if self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updatesInLastSecond'] > self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updatesInLastSecondMaximum']:
                                self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updatesInLastSecondMaximum'] = self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updatesInLastSecond']
                            self.generalLogger.debug(u'=======> RACE DETECTION FOR REMOTE \'{}\': SECONDS SINCE MIDNIGHT = \'{}\', COUNT = \'{}\' [MAX = \'{}\'] <======='.format(newDev.name, seconds, self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updatesInLastSecond'], self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updatesInLastSecondMaximum']))   
                            if self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updatesInLastSecond'] > RACE_CONDITION_LIMIT:
                                self.generalLogger.error(u'Potential race condition detected for Remote Thermostat device managed by TRV Controller \'{}\' in TRV Plugin [deviceUpdated] - TRV Controller device being disabled!'.format(trvControllerDev.name))   
                                indigo.device.enable(trvCtlrDevId, value=False)

                                # setting a timer to re-enable after 60 seconds

                                self.globals['timers']['raceCondition'][trvCtlrDevId] = threading.Timer(60.0, self.deviceRaceConditionReEnableTriggered, [trvCtlrDevId])
                                self.globals['timers']['raceCondition'][trvCtlrDevId].setDaemon(True)
                                self.globals['timers']['raceCondition'][trvCtlrDevId].start()

                                return  # Note that the 'finally:' staement at the end of this method will retun the coreect values to Indigo

                        if 'batteryLevel' in newDev.states:
                            if (origDev.batteryLevel != newDev.batteryLevel) or (self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'] != newDev.batteryLevel):
                                self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'] = newDev.batteryLevel
                                updateRequested = True
                                updateList[UPDATE_REMOTE_BATTERY_LEVEL] = newDev.batteryLevel
                                updateLog[UPDATE_REMOTE_BATTERY_LEVEL] = u'Remote Battery Level updated from {} to {} [Internal store = \'{}\']'.format(origDev.batteryLevel, newDev.batteryLevel, self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'])
                        
                        if trvControllerDev.states['controllerMode'] != self.globals['trvc'][trvCtlrDevId]['controllerMode']:
                                updateRequested = True
                                updateList[UPDATE_CONTROLLER_MODE] = self.globals['trvc'][trvCtlrDevId]['controllerMode']
                                updateLog[UPDATE_CONTROLLER_MODE] = u'Controller Mode updated from {} to {}'.format(CONTROLLER_MODE_TRANSLATION[trvControllerDev.states['controllerMode']], CONTROLLER_MODE_TRANSLATION[self.globals['trvc'][trvCtlrDevId]['controllerMode']])

                        try:
                            origTemp = float(origDev.temperatures[0])
                            newTemp = float(newDev.temperatures[0])  # Remote
                        except AttributeError:
                            try:
                                origTemp = float(origDev.states['sensorValue'])
                                newTemp = float(newDev.states['sensorValue'])  # e.g. Aeon 4 in 1
                            except (AttributeError, KeyError):
                                try:
                                    origTemp = float(origDev.states['temperatureInput1'])
                                    newTemp = float(newDev.states['temperatureInput1'])  # e.g. Secure SRT321 / HRT4-ZW
                                except (AttributeError, KeyError):
                                    try:
                                        origTemp = float(origDev.states['temperature'])
                                        newTemp = float(newDev.states['temperature'])  # e.g. Oregon Scientific Temp Sensor
                                    except (AttributeError, KeyError):
                                        try:
                                            origTemp = float(origDev.states['Temperature'])
                                            newTemp = float(newDev.states['Temperature'])  # e.g. Netatmo
                                        except (AttributeError, KeyError):
                                            try:
                                                origTemp = float(origDev.states['sensorValue'])
                                                newTemp = float(newDev.states['sensorValue'])  # e.g. HeatIT TF021
                                            except (AttributeError, KeyError):
                                                self.generalLogger.error(u'\'{}\' is an unknown Remote Thermostat type - remote support disabled for \'{}\''.format(newDev.name, trvControllerDev.name), isError=True)
                                                del self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['remoteDevId']]  # Disable Remote Support
                                                self.globals['trvc'][trvCtlrDevId]['remoteDevId'] = 0

                        if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:

                            # origTemp should already have had the offset applied - just need to add it to newTemp to ensure comparison is valide

                            newTempPlusOffset = newTemp + float(self.globals['trvc'][trvCtlrDevId]['remoteTempOffset'])  
                            if origTemp != newTempPlusOffset:
                                updateRequested = True
                                updateList[UPDATE_REMOTE_TEMPERATURE] = newTemp  # Send through the original (non-offsetted) temperature
                                updateLog[UPDATE_REMOTE_TEMPERATURE] = u'Temperature updated from {} to {} [Internal store = \'{}\']'.format(origTemp, newTempPlusOffset, self.globals['trvc'][trvCtlrDevId]['temperatureRemote'])
                                if self.globals['trvc'][trvCtlrDevId]['updateCsvFile']:
                                    if self.globals['trvc'][trvCtlrDevId]['updateAllCsvFiles']:
                                        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES, trvCtlrDevId, None])
                                    else:
                                        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['temperatureRemote', newTempPlusOffset]])  # The offset temperature for the CSV file

                            if self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl']:
                                if float(newDev.heatSetpoint) != float(origDev.heatSetpoint):
                                    updateRequested = True
                                    updateList[UPDATE_REMOTE_HEAT_SETPOINT_FROM_DEVICE] = newDev.heatSetpoint
                                    updateLog[UPDATE_REMOTE_HEAT_SETPOINT_FROM_DEVICE] = u'Remote Heat Setpoint changed from {} to {} [Internal store = {}]'.format(origDev.heatSetpoint, newDev.heatSetpoint, self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote'])
                                    if self.globals['trvc'][trvCtlrDevId]['updateCsvFile']:
                                        if self.globals['trvc'][trvCtlrDevId]['updateAllCsvFiles']:
                                            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES, trvCtlrDevId, None])
                                        else:
                                            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['setpointHeatRemote', float(newDev.heatSetpoint)]])

                            if newDev.protocol == indigo.kProtocol.ZWave:
                                # Check if Z-Wave Event has been received
                                if self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountRemote'] > self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountPreviousRemote']:
                                    self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountPreviousRemote'] = self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountRemote']
                                    updateRequested = True
                                    updateList[UPDATE_ZWAVE_EVENT_RECEIVED_REMOTE] = self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeRemote']
                                    updateLog[UPDATE_ZWAVE_EVENT_RECEIVED_REMOTE] = u'Remote Thermostat Z-Wave event received. Time updated to \'{}\'. Received count now totals: {}'.format(self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeRemote'], self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountPreviousRemote'])
         
                                # Check if Z-Wave Event has been sent
                                if self.globals['trvc'][trvCtlrDevId]['zwaveSentCountRemote'] > self.globals['trvc'][trvCtlrDevId]['zwaveSentCountPreviousRemote']:
                                    self.globals['trvc'][trvCtlrDevId]['zwaveSentCountPreviousRemote'] = self.globals['trvc'][trvCtlrDevId]['zwaveSentCountRemote']
                                    updateRequested = True
                                    updateList[UPDATE_ZWAVE_EVENT_SENT_REMOTE] = self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeRemote']
                                    updateLog[UPDATE_ZWAVE_EVENT_SENT_REMOTE] = u'Remote Thermostat Z-Wave event sent. Time updated to \'{}\'. Sent count now totals: {}'.format(self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeRemote'], self.globals['trvc'][trvCtlrDevId]['zwaveSentCountRemote'])
                            else:
                                if newDev.lastSuccessfulComm != self.globals['trvc'][trvCtlrDevId]['lastSuccessfulCommRemote']:
                                    self.globals['trvc'][trvCtlrDevId]['eventReceivedCountRemote'] += 1
                                    updateRequested = True
                                    updateList[UPDATE_EVENT_RECEIVED_REMOTE] = u'{}'.format(newDev.lastSuccessfulComm)
                                    updateLog[UPDATE_EVENT_RECEIVED_REMOTE] = u'Remote Thermostat event received. Time updated to \'{}\'. Received count now totals: {}'.format(newDev.lastSuccessfulComm, self.globals['trvc'][trvCtlrDevId]['eventReceivedCountRemote'])

                            self.globals['trvc'][trvCtlrDevId]['lastSuccessfulCommRemote'] = newDev.lastSuccessfulComm

                    if updateRequested:

                        deviceUpdatedLog = deviceUpdatedLog + '\n==  List of states to be queued for update by TRVHANDLER:'.format(newDev.name)
                        for itemToUpdate in updateList.iteritems():
                            updateKey = itemToUpdate[0]
                            updateValue = itemToUpdate[1]
                            deviceUpdatedLog = deviceUpdatedLog + '\n==    > Description = {}, Value = {}'.format(UPDATE_TRANSLATION[updateKey], updateValue)

                        if self.globals['devicesToTrvControllerTable'][newDev.id]['type'] == TRV:
                            queuedCommand = CMD_UPDATE_TRV_STATES
                        elif self.globals['devicesToTrvControllerTable'][newDev.id]['type'] == VALVE:
                            queuedCommand = CMD_UPDATE_VALVE_STATES
                        else:
                            # Must be Remote
                            queuedCommand = CMD_UPDATE_REMOTE_STATES
                        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, self.globals['deviceUpdatedSequenceCount'], queuedCommand, trvCtlrDevId, [updateList, ]])

                        deviceUpdatedLog = deviceUpdatedLog + '\n==  Description of updates that will be performed by TRVHANDLER:'.format(newDev.name)
                        for itemToUpdate in updateLog.iteritems():
                            updateKey = itemToUpdate[0]
                            updateValue = itemToUpdate[1]
                            deviceUpdatedLog = deviceUpdatedLog + '\n==    > {}'.format(updateValue)

                        deviceUpdatedLog = deviceUpdatedLog + u'\n==\n======================================================================================================================================================\n\n'

                    else:

                        deviceUpdatedLog = deviceUpdatedLog + u'\n==\n== No updates to \'{}\' that are of interest to the plugin'.format(DEVICE_TYPE_TRANSLATION[self.globals['devicesToTrvControllerTable'][newDev.id]['type']])
                        deviceUpdatedLog = deviceUpdatedLog + self.deviceUpdatedList(origDev, newDev, '==')
                        deviceUpdatedLog = deviceUpdatedLog + u'\n==\n======================================================================================================================================================\n\n'
                        deviceUpdatedLog = ''

                    if len(deviceUpdatedLog) > 0:
                        self.generalLogger.debug(deviceUpdatedLog)

                    # else:
                    #

        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [deviceUpdated] for device \'{}\']. Line \'{}\' has error=\'{}\''.format(newDev.name, sys.exc_traceback.tb_lineno, err))   

        finally:

            indigo.PluginBase.deviceUpdated(self, origDev, newDev)


    def deviceUpdatedList(self, origDev, newDev, fillChar):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        return ''
        
        #  DaveL17's code !!!

        # try:
        #     indigo.PluginBase.deviceUpdated(self, origDev, newDev)

        #     # Attribute changes
        #     # exclude_list = ['globalProps', 'lastChanged', 'lastSuccessfulComm', 'ownerProps', 'states']
        #     exclude_list = ['globalProps', 'ownerProps', 'states']
        #     attrib_list = [attr for attr in dir(origDev) if not callable(getattr(origDev, attr)) and '__' not in attr and attr not in exclude_list]
        #     attrib_dict = {attrib: (getattr(origDev, attrib), getattr(newDev, attrib)) for attrib in attrib_list if getattr(origDev, attrib) != getattr(newDev, attrib)}

        #     # Property changes
        #     orig_props = dict(origDev.ownerProps)
        #     new_props = dict(newDev.ownerProps)
        #     props_dict = {key: (orig_props[key], new_props[key]) for key in orig_props if orig_props[key] != new_props[key]}

        #     # State changes
        #     state_dict = {key: (origDev.states[key], val) for key, val in newDev.states.iteritems() if key not in origDev.states or val != origDev.states[key]}

        #     updateInfo = u'\n{}  PLUGIN: List of uninteresting and ignored updates:'.format(fillChar)
        #     if len(attrib_dict) > 0 or len(state_dict) > 0 or len(props_dict) > 0:
        #         updateInfo = updateInfo + u'\n{}  > Attr: {}'.format(fillChar, attrib_dict)
        #         updateInfo = updateInfo + u'\n{}  > Props: {}'.format(fillChar, props_dict)
        #         updateInfo = updateInfo + u'\n{}  > States: {}'.format(fillChar, state_dict)
        #     else:
        #         updateInfo = updateInfo + (u'\n{}  > *** NOTHING CHANGED? ***').format(fillChar)
        #     return updateInfo

        # except StandardError, err:
        #     errorDetected = u'StandardError detected in TRV Plugin [deviceUpdatedList] for device \'{}\']. Line \'{}\' has error=\'{}\''.format(newDev.name, sys.exc_traceback.tb_lineno, err)  
        #     return '{}  > {}'.format(fillChar, errorDetected)

    def getActionConfigUiValues(self, valuesDict, typeId, actionId):
        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.generalLogger.debug(u'getActionConfigUiValues: typeId [{}], actionId [{}], pluginProps[{}]'.format(typeId, actionId, valuesDict))

        modifiedValuesDict = valuesDict
        errorDict = indigo.Dict()

        if typeId == "processUpdateSchedule":
            devId = actionId  # TRV Controller Device Id

        elif typeId == "processBoost":
            boostMode = int(valuesDict.get('boostMode', BOOST_MODE_NOT_SET))
            if boostMode == BOOST_MODE_NOT_SET:
                valuesDict['boostMode'] = str(BOOST_MODE_SELECT)

        return valuesDict, errorDict

    def actionConfigApplyDefaultScheduleValues(self, valuesDict, typeId, actionId):
        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.generalLogger.debug(u'actionConfigApplyDefaultScheduleValues: typeId[{}], actionId[{}], ValuesDict:\n{}\''.format(typeId, actionId, valuesDict))

        modifiedValuesDict = valuesDict

        devId = actionId  # TRV Controller Device Id

        valuesDict['schedule1Enabled'] = self.globals['trvc'][devId]['scheduleReset1Enabled']
        valuesDict['schedule1TimeOn']  = self.globals['trvc'][devId]['scheduleReset1TimeOn'] 
        valuesDict['schedule1TimeOff'] = self.globals['trvc'][devId]['scheduleReset1TimeOff']
        valuesDict['schedule1SetpointHeat'] = self.globals['trvc'][devId]['scheduleReset1HeatSetpoint'] 
        valuesDict['schedule2Enabled'] = self.globals['trvc'][devId]['scheduleReset2Enabled']
        valuesDict['schedule2TimeOn']  = self.globals['trvc'][devId]['scheduleReset2TimeOn'] 
        valuesDict['schedule2TimeOff'] = self.globals['trvc'][devId]['scheduleReset2TimeOff'] 
        valuesDict['schedule2SetpointHeat'] = self.globals['trvc'][devId]['scheduleReset2HeatSetpoint'] 
        valuesDict['schedule3Enabled'] = self.globals['trvc'][devId]['scheduleReset3Enabled']
        valuesDict['schedule3TimeOn']  = self.globals['trvc'][devId]['scheduleReset3TimeOn'] 
        valuesDict['schedule3TimeOff'] = self.globals['trvc'][devId]['scheduleReset3TimeOff'] 
        valuesDict['schedule3SetpointHeat'] = self.globals['trvc'][devId]['scheduleReset3HeatSetpoint'] 
        valuesDict['schedule4Enabled'] = self.globals['trvc'][devId]['scheduleReset4Enabled']
        valuesDict['schedule4TimeOn']  = self.globals['trvc'][devId]['scheduleReset4TimeOn'] 
        valuesDict['schedule4TimeOff'] = self.globals['trvc'][devId]['scheduleReset4TimeOff'] 
        valuesDict['schedule4SetpointHeat'] = self.globals['trvc'][devId]['scheduleReset4HeatSetpoint'] 

        return valuesDict

    def validateActionConfigUi(self, valuesDict, typeId, actionId):

        try:
            self.methodTracer.threaddebug(u'Main Plugin Method')

            self.generalLogger.debug(u'Validate Action Config UI: typeId = \'{}\', actionId = \'{}\', ValuesDict =\n{}\n'.format(typeId, actionId, valuesDict))

            if typeId == "processUpdateSchedule":

                valuesDict['setpointHeatMinimum'] = float(self.globals['trvc'][actionId]['setpointHeatMinimum'])
                valuesDict['setpointHeatMaximum'] = float(self.globals['trvc'][actionId]['setpointHeatMaximum'])

                # Validate Schedule 1
                schedule1Enabled = bool(valuesDict.get('schedule1Enabled', False))
                if schedule1Enabled:
                    scheduleValid, scheduleData = self.validateSchedule(actionId, valuesDict, '1')
                    if not scheduleValid:
                        return (False, valuesDict, scheduleData)  # i.e. False, valuesDict, errorDict
                    schedule1TimeOn = scheduleData[0]
                    schedule1TimeOff = scheduleData[1]
                    schedule1SetpointHeat = scheduleData[2]

                # Validate Schedule 2
                schedule2Enabled = bool(valuesDict.get('schedule2Enabled', False))
                if schedule2Enabled:
                    scheduleValid, scheduleData = self.validateSchedule(actionId, valuesDict, '2')
                    if not scheduleValid:
                        return (False, valuesDict, scheduleData)  # i.e. False, valuesDict, errorDict
                    schedule2TimeOn = scheduleData[0]
                    schedule2TimeOff = scheduleData[1]
                    schedule2SetpointHeat = scheduleData[2]

                # Validate Schedule 3
                schedule3Enabled = bool(valuesDict.get('schedule3Enabled', False))
                if schedule3Enabled:
                    scheduleValid, scheduleData = self.validateSchedule(actionId, valuesDict, '3')
                    if not scheduleValid:
                        return (False, valuesDict, scheduleData)  # i.e. False, valuesDict, errorDict
                    schedule3TimeOn = scheduleData[0]
                    schedule3TimeOff = scheduleData[1]
                    schedule3SetpointHeat = scheduleData[2]

                # Validate Schedule 4
                schedule4Enabled = bool(valuesDict.get('schedule4Enabled', False))
                if schedule4Enabled:
                    scheduleValid, scheduleData = self.validateSchedule(actionId, valuesDict, '4')
                    if not scheduleValid:
                        return (False, valuesDict, scheduleData)  # i.e. False, valuesDict, errorDict
                    schedule4TimeOn = scheduleData[0]
                    schedule4TimeOff = scheduleData[1]
                    schedule4SetpointHeat = scheduleData[2]

                # Consistency check across schedules
                if schedule1Enabled:
                    if schedule2Enabled:
                        if schedule1TimeOff < schedule2TimeOn:
                            secondsDelta = secondsFromHHMM(schedule2TimeOn) - secondsFromHHMM(schedule1TimeOff)
                        else:
                            secondsDelta = 0
                        if secondsDelta < 600:  # 10 minutes (600 seconds) check
                            errorDict = indigo.Dict()
                            errorDict['schedule1TimeOff'] = 'The Schedule One heating OFF time must end before the Schedule Two heating ON time'
                            errorDict['schedule2TimeOn'] = 'The Schedule Two heating On time must start after the Schedule One heating Off time'
                            errorDict['showAlertText'] = 'The Schedule One OFF time [{}] must be before the Schedule Two ON time [{}] and there must be at least 10 minutes between the Schedule One OFF time and Schedule Two ON time.'.format(schedule1TimeOff, schedule2TimeOn)
                            return (False, valuesDict, errorDict)
                    if schedule3Enabled:
                        if schedule1TimeOff < schedule3TimeOn:
                            secondsDelta = secondsFromHHMM(schedule3TimeOn) - secondsFromHHMM(schedule1TimeOff)
                        else:
                            secondsDelta = 0
                        if secondsDelta < 600:  # 10 minutes (600 seconds) check
                            errorDict = indigo.Dict()
                            errorDict['schedule1TimeOff'] = 'The Schedule One heating OFF time must end before the Schedule Three heating ON time'
                            errorDict['schedule3TimeOn'] = 'The Schedule Three heating On time must start after the Schedule One heating Off time'
                            errorDict['showAlertText'] = 'The Schedule One OFF time [{}] must be before the Schedule Three ON time [{}] and there must be at least 10 minutes between the Schedule One OFF time and Schedule Three ON time.'.format(schedule1TimeOff, schedule3TimeOn)
                            return (False, valuesDict, errorDict)
                    if schedule4Enabled:
                        if schedule1TimeOff < schedule4TimeOn:
                            secondsDelta = secondsFromHHMM(schedule4TimeOn) - secondsFromHHMM(schedule1TimeOff)
                        else:
                            secondsDelta = 0
                        if secondsDelta < 600:  # 10 minutes (600 seconds) check
                            errorDict = indigo.Dict()
                            errorDict['schedule1TimeOff'] = 'The Schedule One heating OFF time must end before the Schedule Four heating ON time'
                            errorDict['schedule4TimeOn'] = 'The Schedule Four heating On time must start after the Schedule One heating Off time'
                            errorDict['showAlertText'] = 'The Schedule One OFF time [{}] must be before the Schedule Four ON time [{}] and there must be at least 10 minutes between the Schedule One OFF time and Schedule Four ON time.'.format(schedule1TimeOff, schedule4TimeOn)
                            return (False, valuesDict, errorDict)

                if schedule2Enabled:
                    if schedule3Enabled:
                        if schedule2TimeOff < schedule3TimeOn:
                            secondsDelta = secondsFromHHMM(schedule3TimeOn) - secondsFromHHMM(schedule2TimeOff)
                        else:
                            secondsDelta = 0
                        if secondsDelta < 600:  # 10 minutes (600 seconds) check
                            errorDict = indigo.Dict()
                            errorDict['schedule2TimeOff'] = 'The Schedule Two heating OFF time must end before the Schedule Three heating ON time'
                            errorDict['schedule3TimeOn'] = 'The Schedule Three heating On time must start after the Schedule Two heating Off time'
                            errorDict['showAlertText'] = 'The Schedule Two OFF time [{}] must be before the Schedule Three ON time [{}] and there must be at least 10 minutes between the Schedule Two OFF time and Schedule Three ON time.'.format(schedule2TimeOff, schedule3TimeOn)
                            return (False, valuesDict, errorDict)
                               
                    if schedule4Enabled:
                        if schedule2TimeOff < schedule4TimeOn:
                            secondsDelta = secondsFromHHMM(schedule4TimeOn) - secondsFromHHMM(schedule2TimeOff)
                        else:
                            secondsDelta = 0
                        if secondsDelta < 600:  # 10 minutes (600 seconds) check
                            errorDict = indigo.Dict()
                            errorDict['schedule2TimeOff'] = 'The Schedule Two heating OFF time must end before the Schedule Four heating ON time'
                            errorDict['schedule4TimeOn'] = 'The Schedule Four heating On time must start after the Schedule Two heating Off time'
                            errorDict['showAlertText'] = 'The Schedule Two OFF time [{}] must be before the Schedule Four ON time [{}] and there must be at least 10 minutes between the Schedule Two OFF time and Schedule Four ON time.'.format(schedule2TimeOff, schedule4TimeOn)
                            return (False, valuesDict, errorDict)
                               
                if schedule3Enabled:
                    if schedule4Enabled:
                        if schedule3TimeOff < schedule4TimeOn:
                            secondsDelta = secondsFromHHMM(schedule4TimeOn) - secondsFromHHMM(schedule3TimeOff)
                        else:
                            secondsDelta = 0
                        if secondsDelta < 600:  # 10 minutes (600 seconds) check
                            errorDict = indigo.Dict()
                            errorDict['schedule3TimeOff'] = 'The Schedule Three heating OFF time must end before the Schedule Four heating ON time'
                            errorDict['schedule4TimeOn'] = 'The Schedule Four heating On time must start after the Schedule Three heating Off time'
                            errorDict['showAlertText'] = 'The Schedule Three OFF time [{}] must be before the Schedule Four ON time [{}] and there must be at least 10 minutes between the Schedule Three OFF time and Schedule Four ON time.'.format(schedule2TimeOff, schedule4TimeOn)
                            return (False, valuesDict, errorDict)

            elif typeId == "processBoost":

                boostMode = int(valuesDict.get('boostMode', 0))
                if boostMode == BOOST_MODE_SELECT:
                    errorDict = indigo.Dict()
                    errorDict['boostMode'] = 'You must select a boost mode: \'Delta T\' or \'Setpoint\'.'
                    errorDict['showAlertText'] = 'You must select a boost mode: \'Delta T\' or \'Setpoint\'.'
                    return (False, valuesDict, errorDict)

                if boostMode == BOOST_MODE_DELTA_T:  # Validate deltaT
                    valid = False
                    try:
                        boostDeltaT = float(valuesDict.get('boostDeltaT', 3))
                        valid = True
                    except:
                        pass

                    if not valid or boostDeltaT < 1 or boostDeltaT > 5 or boostDeltaT % 0.5 != 0:
                        errorDict = indigo.Dict()
                        errorDict['boostDeltaT'] = 'Boost Delta T must be a numeric value between 1 and 5 (inclusive) e.g 2.5'
                        errorDict['showAlertText'] = 'You must enter a valid Delta T to boost the temperature by. It must be set between 1 and 5 (inclusive) and a multiple of 0.5.'
                        return (False, valuesDict, errorDict)

                else:  # Validate Setpoint

                    valid = False
                    try:
                        boostSetpoint = float(valuesDict.get('boostSetpoint', 3.0))
                        valid = True
                    except:
                        pass

                    if actionId in self.globals['trvc']:
                        setpointHeatMinimum = float(self.globals['trvc'][actionId]['setpointHeatMinimum'])
                        setpointHeatMaximum = float(self.globals['trvc'][actionId]['setpointHeatMaximum'])
                    else:
                        errorDict = indigo.Dict()
                        errorDict['boostSetpoint'] = 'Unable to test Setpoint temperature against permissable minimum/maximum.'
                        errorDict['showAlertText'] = 'Unable to test Setpoint temperature against permissable minimum/maximum - make sure device \'{}\' is enabled.'.format(indigo.devices[actionId].name)
                        return (False, valuesDict, errorDict)

                    if not valid or boostSetpoint < setpointHeatMinimum or boostSetpoint > setpointHeatMaximum or boostSetpoint % 0.5 != 0:
                        errorDict = indigo.Dict()
                        errorDict['boostSetpoint'] = 'Setpoint temperature must be numeric and set between {} and {} (inclusive)'.format(setpointHeatMinimum, setpointHeatMaximum)
                        errorDict['showAlertText'] = 'You must enter a valid Setpoint temperature for the TRV. It must be numeric and set between {} and {} (inclusive) and a multiple of 0.5.'.format(setpointHeatMinimum, setpointHeatMaximum)
                        return (False, valuesDict, errorDict)

                valid = False
                try:
                    boostMinutes = int(valuesDict.get('boostMinutes', 20))
                    valid = True
                except:
                    pass

                if not valid or boostMinutes < 5 or boostMinutes > 120:
                    errorDict = indigo.Dict()
                    errorDict['boostMinutes'] = 'Boost Minutes must be an integer and set between 5 and 120 (inclusive) e.g 20'
                    errorDict['showAlertText'] = 'You must enter a valid number of minutes to boost the temperature by. It must be a numeric value and set between 5 and 120 (inclusive).'
                    return (False, valuesDict, errorDict)

            elif typeId == "processExtend":

                # Validate extend increment minutes
                valid = False
                try:
                    extendIncrementMinutes = int(valuesDict.get('extendIncrementMinutes', 15))
                    valid = True
                except:
                    pass
                if not valid or extendIncrementMinutes < 15 or extendIncrementMinutes > 60:
                    errorDict = indigo.Dict()
                    errorDict["extendIncrementMinutes"] = "The Extend Increment Minutes must be an integer and set between 15 and 60 (inclusive)"
                    errorDict['showAlertText'] = "You must enter a valid Extend Increment Minutes (length of time to increase extend by) for the TRV. It must be an integer and set between 15 and 60 (inclusive)."
                    return (False, valuesDict, errorDict)

                # Validate extend maximum minutes
                valid = False
                try:
                    extendMaximumMinutes = int(valuesDict.get('extendMaximumMinutes', 15))
                    valid = True
                except:
                    pass

                if not valid or extendMaximumMinutes < 15 or extendMaximumMinutes > 1080:
                    errorDict = indigo.Dict()
                    errorDict["extendMaximumMinutes"] = "The Extend Maximum Minutes must be an integer and set between 15 and 1080 (18 hours!) (inclusive)"
                    errorDict['showAlertText'] = "You must enter a valid Extend Maximum Minutes (maximum length of time to extend by) for the TRV. It must be an integer and set between 15 and 1080 (18 hours!) (inclusive)."
                    return (False, valuesDict, errorDict)

            elif typeId == "processUpdateAllCsvFilesViaPostgreSQL":

                # Validate Override Default Retention Hours
                valid = False
                try:
                    overrideDefaultRetentionHours = valuesDict.get('overrideDefaultRetentionHours', '')
                    if overrideDefaultRetentionHours == '':
                        overrideDefaultRetentionHours = 1024  # A random large number for validity check
                        valid = True
                    else:
                        overrideDefaultRetentionHours = int(valuesDict.get('overrideDefaultRetentionHours', ''))
                        valid = True
                except:
                    pass

                if not valid or overrideDefaultRetentionHours <= 1:
                    errorDict = indigo.Dict()
                    errorDict["overrideDefaultRetentionHours"] = "The Override Default Retention Hours must be blank or an integer greater than 0"
                    errorDict['showAlertText'] = "You must leave the Override Default Retention Hours blank or enter a valid Retention Hours to retain the CSV data. If set it must be an integer and greater than zero."
                    return (False, valuesDict, errorDict)

            return (True, valuesDict)

        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [validateActionConfigUi]. Line \'{}\' has error=\'{}\''.format(indigo.devices[actionId].name, sys.exc_traceback.tb_lineno, err))   

    
    def actionControlThermostat(self, action, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.generalLogger.debug(u' Thermostat \'{}\', Action received: \'{}\''.format(dev.name, action.description))
        self.generalLogger.debug(u'... Action details:\n{}\n'.format(action))


        ###### SET HVAC MODE ######
        if action.thermostatAction == indigo.kThermostatAction.SetHvacMode:
            hvacMode = action.actionMode
            if hvacMode == HVAC_COOL or hvacMode == HVAC_AUTO:  # Don't allow HVAC Mode of Cool or Auto
                self.generalLogger.error(u'TRV Controller  \'{}\' doesn\'t allow action \'{}\' - request ignored'.format(dev.name, action.description))
            else:
                dev.updateStateOnServer('hvacOperationMode', action.actionMode)

        ###### DECREASE HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.DecreaseHeatSetpoint:
            newSetpoint = dev.heatSetpoint - action.actionValue
            keyValueList = [
                    {'key': 'controllerMode', 'value': CONTROLLER_MODE_UI},
                    {'key': 'controllerModeUi', 'value':  CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_UI]},
                    {'key': 'setpointHeat', 'value': newSetpoint}
                ]
            dev.updateStatesOnServer(keyValueList)

        ###### INCREASE HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.IncreaseHeatSetpoint:
            newSetpoint = dev.heatSetpoint + action.actionValue
            keyValueList = [
                    {'key': 'controllerMode', 'value': CONTROLLER_MODE_UI},
                    {'key': 'controllerModeUi', 'value':  CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_UI]},
                    {'key': 'setpointHeat', 'value': newSetpoint}
                ]
            dev.updateStatesOnServer(keyValueList)

        ###### SET HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.SetHeatSetpoint:
            newSetpoint = action.actionValue
            keyValueList = [
                    {'key': 'controllerMode', 'value': CONTROLLER_MODE_UI},
                    {'key': 'controllerModeUi', 'value':  CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_UI]},
                    {'key': 'setpointHeat', 'value': newSetpoint}
                ]
            dev.updateStatesOnServer(keyValueList)

        ###### REQUEST STATUS ALL ETC ######
        elif action.thermostatAction in [indigo.kThermostatAction.RequestStatusAll, indigo.kThermostatAction.RequestMode,
                indigo.kThermostatAction.RequestEquipmentState, indigo.kThermostatAction.RequestTemperatures, indigo.kThermostatAction.RequestHumidities,
                indigo.kThermostatAction.RequestDeadbands, indigo.kThermostatAction.RequestSetpoints]:
            if self.globals['trvc'][action.deviceId]['trvDevId'] != 0:
                indigo.device.statusRequest(self.globals['trvc'][action.deviceId]['trvDevId'])
            if self.globals['trvc'][action.deviceId]['remoteDevId'] != 0:
                indigo.device.statusRequest(self.globals['trvc'][action.deviceId]['remoteDevId'])
        else:
            self.generalLogger.error(u'Unknown Action for TRV Controller \'{}\': Action \'{}\' Ignored'.format(dev.name, action.description))

    # def processLimeProtection(self, pluginAction):

    #     self.limeProtectionRequested = True

    # def processCancelLimeProtection(self, pluginAction):

    #     if not self.limeProtectionActive:
    #         self.generalLogger.info("Lime Protection not active - Cancel request ignored.")
    #         return

    #     self.limeProtectionRequested = False

    def processTurnOn(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        newSetpoint = float(self.globals['trvc'][dev.Id]['setpointHeatDefault'])

        keyValueList = [
        {'key': 'controllerMode', 'value': CONTROLLER_MODE_UI},
        {'key': 'controllerModeUi', 'value':  CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_UI]},
        {'key': 'setpointHeat', 'value': newSetpoint}
            ]
        dev.updateStatesOnServer(keyValueList)

    def processTurnOff(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        newSetpoint = float(self.globals['trvc'][dev.Id]['setpointHeatMinimum'])

        keyValueList = [
        {'key': 'controllerMode', 'value': CONTROLLER_MODE_UI},
        {'key': 'controllerModeUi', 'value':  CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_UI]},
        {'key': 'setpointHeat', 'value': newSetpoint}
            ]
        dev.updateStatesOnServer(keyValueList)

    def processToggleTurnOnOff(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        if float(self.globals['trvc'][dev.id]['setpointHeat']) == float(self.globals['trvc'][dev.id]['setpointHeatMinimum']):
            self.processTurnOn(pluginAction, dev)
        else:
            self.processTurnOff(pluginAction, dev)

    def processAdvance(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_ADVANCE, dev.id, [ADVANCE_NEXT]])

    def processAdvanceOn(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_ADVANCE, dev.id, [ADVANCE_NEXT_ON]])

    def processAdvanceOff(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_ADVANCE, dev.id, [ADVANCE_NEXT_OFF]])

    def processCancelAdvance(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_ADVANCE_CANCEL, dev.id, [True]])

    def processAdvanceToggle(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        if not self.globals['trvc'][dev.id]['advanceActive']:
            self.processAdvance(pluginAction, dev)
        else:
            self.processCancelAdvance(pluginAction, dev)

    def processAdvanceOnToggle(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        if not self.globals['trvc'][dev.id]['advanceActive']:
            self.processAdvanceOn(pluginAction, dev)
        else:
            self.processCancelAdvance(pluginAction, dev)

    def processAdvanceOffToggle(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        if not self.globals['trvc'][dev.id]['advanceActive']:
            self.processAdvanceOff(pluginAction, dev)
        else:
            self.processCancelAdvance(pluginAction, dev)

    def processBoost(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        if pluginAction.pluginTypeId == 'processBoost':
            boostMode = int(pluginAction.props.get('boostMode', 0))
        elif pluginAction.pluginTypeId == 'processBoostToggle':
            boostMode = int(pluginAction.props.get('toggleBoostMode', 0))
        else:
            self.generalLogger.error(u'Boost logic failure for thermostat \'{}\' - boost not actioned for id \'{}\''.format(dev.name, pluginAction))
            return

        if boostMode == BOOST_MODE_SELECT:
            self.generalLogger.error(u'Boost Mode not set for thermostat \'{}\' - boost not actioned'.format(dev.name))
            return

        if pluginAction.pluginTypeId == 'processBoost':
            boostDeltaT = float(pluginAction.props.get('boostDeltaT', 2.0))
            boostSetpoint =  float(pluginAction.props.get('boostSetpoint', 21.0))
            boostMinutes = int(pluginAction.props.get('boostMinutes', 20))
        else:  # Must be pluginAction = processBoostToggle
            boostDeltaT = float(pluginAction.props.get('toggleBoostDeltaT', 2.0))
            boostSetpoint =  float(pluginAction.props.get('toggleBoostSetpoint', 21.0))
            boostMinutes = int(pluginAction.props.get('toggleBoostMinutes', 20))

        self.globals['trvc'][dev.id]['boostActive'] = True

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_BOOST, dev.id, [boostMode, boostDeltaT, boostSetpoint, boostMinutes]])

        if boostMode == BOOST_MODE_DELTA_T:
            self.generalLogger.info(u'Boost actioned for {} minutes with a Delta T of {} degrees for thermostat \'{}\''.format(boostMinutes, boostDeltaT, dev.name))
        else:  # BOOST_MODE_SETPOINT
            self.generalLogger.info(u'Boost actioned for {} minutes with a Setpoint of {} degrees for thermostat \'{}\''.format(boostMinutes, boostSetpoint, dev.name))

    def processCancelBoost(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        if self.globals['trvc'][dev.id]['boostActive']:
            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_BOOST_CANCEL, dev.id, [True]])
            self.generalLogger.info(u'Boost cancelled for thermostat \'{}\''.format(dev.name))
        else:
            self.generalLogger.info(u'Boost cancel request ignored for thermostat \'{}\' as no Boost active'.format(dev.name))

    def processBoostToggle(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        if not self.globals['trvc'][dev.id]['boostActive']:
            self.processBoost(pluginAction, dev)
        else:
            self.processCancelBoost(pluginAction, dev)

    def processExtend(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        extendIncrementMinutes = int(pluginAction.props.get('extendIncrementMinutes', 15))
        extendMaximumMinutes =  int(pluginAction.props.get('extendMaximumMinutes', 15))

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_EXTEND, dev.id, [extendIncrementMinutes, extendMaximumMinutes]])

        # self.generalLogger.info(u'Extend actioned for thermostat \'{}\''.format(dev.name))

    def processCancelExtend(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        if self.globals['trvc'][dev.id]['extendActive']:
            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_EXTEND_CANCEL, dev.id, [True]])
            # self.generalLogger.info(u'Extend cancelled for thermostat \'{}\''.format(dev.name))
        else:
            self.generalLogger.info(u'Extend cancel request ignored for thermostat \'{}\' as no Extend active'.format(dev.name))

    def processResetScheduleToDeviceDefaults(self, pluginAction, dev):
        
        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.generalLogger.debug(u' Thermostat \'{}\', Action received: \'{}\''.format(dev.name, pluginAction.description))
        self.generalLogger.debug(u'... Action details:\n{}\n'.format(pluginAction))

        devId = dev.id

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_RESET_SCHEDULE_TO_DEVICE_DEFAULTS, devId, None])

    def processUpdateSchedule(self, pluginAction, dev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.generalLogger.debug(u' Thermostat \'{}\', Action received: \'{}\''.format(dev.name, pluginAction.description))
        self.generalLogger.debug(u'... Action details:\n{}\n'.format(pluginAction))

        devId = dev.id

        self.globals['trvc'][devId]['nextScheduleExecutionTime'] = 'Not yet evaluated'
        self.globals['trvc'][devId]['schedule1Enabled'] = bool(pluginAction.props.get('schedule1Enabled', False))
        self.globals['trvc'][devId]['schedule1TimeOn']  = pluginAction.props.get('schedule1TimeOn', '00:00')
        self.globals['trvc'][devId]['schedule1TimeOff'] = pluginAction.props.get('schedule1TimeOff', '00:00')
        self.globals['trvc'][devId]['schedule1SetpointHeat'] = pluginAction.props.get('schedule1SetpointHeat', 0.00) 
        self.globals['trvc'][devId]['schedule2Enabled'] =  bool(pluginAction.props.get('schedule2Enabled', False))
        self.globals['trvc'][devId]['schedule2TimeOn']  = pluginAction.props.get('schedule2TimeOn', '00:00')
        self.globals['trvc'][devId]['schedule2TimeOff'] = pluginAction.props.get('schedule2TimeOff', '00:00')
        self.globals['trvc'][devId]['schedule2SetpointHeat'] = pluginAction.props.get('schedule2SetpointHeat', 0.00) 
        self.globals['trvc'][devId]['schedule3Enabled'] = bool(pluginAction.props.get('schedule3Enabled', False))
        self.globals['trvc'][devId]['schedule3TimeOn']  = pluginAction.props.get('schedule3TimeOn', '00:00')
        self.globals['trvc'][devId]['schedule3TimeOff'] = pluginAction.props.get('schedule3TimeOff', '00:00')
        self.globals['trvc'][devId]['schedule3SetpointHeat'] = pluginAction.props.get('schedule3SetpointHeat', 0.00)
        self.globals['trvc'][devId]['schedule4Enabled'] = bool(pluginAction.props.get('schedule4Enabled', False))
        self.globals['trvc'][devId]['schedule4TimeOn']  = pluginAction.props.get('schedule4TimeOn', '00:00')
        self.globals['trvc'][devId]['schedule4TimeOff'] = pluginAction.props.get('schedule4TimeOff', '00:00')
        self.globals['trvc'][devId]['schedule4SetpointHeat'] = pluginAction.props.get('schedule4SetpointHeat', 0.00)

        if not self.globals['trvc'][devId]['schedule1Enabled'] or self.globals['trvc'][devId]['schedule1SetpointHeat'] == 0.0:
            self.globals['trvc'][devId]['schedule1SetpointHeatUi'] = 'Not Set'
            self.globals['trvc'][devId]['schedule1TimeUi'] = 'Inactive'
        else:
            self.globals['trvc'][devId]['schedule1SetpointHeatUi'] = '{} °C'.format(self.globals['trvc'][devId]['schedule1SetpointHeat'])
            self.globals['trvc'][devId]['schedule1TimeUi'] = '{} - {}'.format(self.globals['trvc'][devId]['schedule1TimeOn'], self.globals['trvc'][devId]['schedule1TimeOff'])

        if not self.globals['trvc'][devId]['schedule2Enabled'] or self.globals['trvc'][devId]['schedule2SetpointHeat'] == 0.0:
            self.globals['trvc'][devId]['schedule2SetpointHeatUi'] = 'Not Set'
            self.globals['trvc'][devId]['schedule2TimeUi'] = 'Inactive'
        else:
            self.globals['trvc'][devId]['schedule2SetpointHeatUi'] = '{} °C'.format(self.globals['trvc'][devId]['schedule2SetpointHeat'])
            self.globals['trvc'][devId]['schedule2TimeUi'] = '{} - {}'.format(self.globals['trvc'][devId]['schedule2TimeOn'], self.globals['trvc'][devId]['schedule2TimeOff'])

        if not self.globals['trvc'][devId]['schedule3Enabled'] or self.globals['trvc'][devId]['schedule3SetpointHeat'] == 0.0:
            self.globals['trvc'][devId]['schedule3SetpointHeatUi'] = 'Not Set'
            self.globals['trvc'][devId]['schedule3TimeUi'] = 'Inactive'
        else:
            self.globals['trvc'][devId]['schedule3SetpointHeatUi'] = '{} °C'.format(self.globals['trvc'][devId]['schedule3SetpointHeat'])
            self.globals['trvc'][devId]['schedule3TimeUi'] = '{} - {}'.format(self.globals['trvc'][devId]['schedule3TimeOn'], self.globals['trvc'][devId]['schedule3TimeOff'])

        if not self.globals['trvc'][devId]['schedule4Enabled'] or self.globals['trvc'][devId]['schedule4SetpointHeat'] == 0.0:
            self.globals['trvc'][devId]['schedule4SetpointHeatUi'] = 'Not Set'
            self.globals['trvc'][devId]['schedule4TimeUi'] = 'Inactive'
        else:
            self.globals['trvc'][devId]['schedule4SetpointHeatUi'] = '{} °C'.format(self.globals['trvc'][devId]['schedule4SetpointHeat'])
            self.globals['trvc'][devId]['schedule4TimeUi'] = '{} - {}'.format(self.globals['trvc'][devId]['schedule4TimeOn'], self.globals['trvc'][devId]['schedule4TimeOff'])

        self.keyValueList = [
                {'key': 'nextScheduleExecutionTime', 'value': self.globals['trvc'][devId]['nextScheduleExecutionTime']},
                {'key': 'schedule1Active', 'value': self.globals['trvc'][devId]['schedule1Active']},
                {'key': 'schedule1Enabled', 'value': self.globals['trvc'][devId]['schedule1Enabled']},
                {'key': 'schedule1TimeOn', 'value': self.globals['trvc'][devId]['schedule1TimeOn']},
                {'key': 'schedule1TimeOff', 'value': self.globals['trvc'][devId]['schedule1TimeOff']},
                {'key': 'schedule1TimeUi', 'value': self.globals['trvc'][devId]['schedule1TimeUi']},
                {'key': 'schedule1SetpointHeat', 'value': self.globals['trvc'][devId]['schedule1SetpointHeatUi']},
                {'key': 'schedule2Active', 'value': self.globals['trvc'][devId]['schedule2Active']},
                {'key': 'schedule2Enabled', 'value': self.globals['trvc'][devId]['schedule2Enabled']},
                {'key': 'schedule2TimeOn', 'value': self.globals['trvc'][devId]['schedule2TimeOn']},
                {'key': 'schedule2TimeOff', 'value': self.globals['trvc'][devId]['schedule2TimeOff']},
                {'key': 'schedule2TimeUi', 'value': self.globals['trvc'][devId]['schedule2TimeUi']},
                {'key': 'schedule2SetpointHeat', 'value': self.globals['trvc'][devId]['schedule2SetpointHeatUi']},
                {'key': 'schedule3Active', 'value': self.globals['trvc'][devId]['schedule3Active']},
                {'key': 'schedule3Enabled', 'value': self.globals['trvc'][devId]['schedule3Enabled']},
                {'key': 'schedule3TimeOn', 'value': self.globals['trvc'][devId]['schedule3TimeOn']},
                {'key': 'schedule3TimeOff', 'value': self.globals['trvc'][devId]['schedule3TimeOff']},
                {'key': 'schedule3TimeUi', 'value': self.globals['trvc'][devId]['schedule3TimeUi']},
                {'key': 'schedule3SetpointHeat', 'value': self.globals['trvc'][devId]['schedule3SetpointHeatUi']},
                {'key': 'schedule4Active', 'value': self.globals['trvc'][devId]['schedule4Active']},
                {'key': 'schedule4Enabled', 'value': self.globals['trvc'][devId]['schedule4Enabled']},
                {'key': 'schedule4TimeOn', 'value': self.globals['trvc'][devId]['schedule4TimeOn']},
                {'key': 'schedule4TimeOff', 'value': self.globals['trvc'][devId]['schedule4TimeOff']},
                {'key': 'schedule4TimeUi', 'value': self.globals['trvc'][devId]['schedule4TimeUi']},
                {'key': 'schedule4SetpointHeat', 'value': self.globals['trvc'][devId]['schedule4SetpointHeatUi']}
            ]
        dev.updateStatesOnServer(self.keyValueList)

        # Set-up schedules
        self.globals['schedules'][devId]['running'] = dict()
        scheduleSetpointOff = float(self.globals['trvc'][devId]['setpointHeatMinimum'])
        self.globals['schedules'][devId]['running'][0] = ('00:00', scheduleSetpointOff, 0, False)  # Start of Day
        self.globals['schedules'][devId]['running'][240000] = ('24:00', scheduleSetpointOff, 9, False)  # End of Day

        if self.globals['trvc'][devId]['schedule1Enabled']:
            scheduleTimeOnUi = self.globals['trvc'][devId]['schedule1TimeOn']
            scheduleTimeOn = int(scheduleTimeOnUi.replace(':','')) * 100  # Add in Seconds
            scheduleTimeOffUi =self.globals['trvc'][devId]['schedule1TimeOff']
            scheduleTimeOff = int(scheduleTimeOffUi.replace(':','')) * 100  # Add in Seconds
            scheduleSetpointOn = float(self.globals['trvc'][devId]['schedule1SetpointHeat'])
            self.globals['schedules'][devId]['running'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 1, True)
            self.globals['schedules'][devId]['running'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 1, False)

        if self.globals['trvc'][devId]['schedule2Enabled']:
            scheduleTimeOnUi = self.globals['trvc'][devId]['schedule2TimeOn']
            scheduleTimeOn = int(scheduleTimeOnUi.replace(':','')) * 100  # Add in Seconds
            scheduleTimeOffUi =self.globals['trvc'][devId]['schedule2TimeOff']
            scheduleTimeOff = int(scheduleTimeOffUi.replace(':','')) * 100  # Add in Seconds
            scheduleSetpointOn = float(self.globals['trvc'][devId]['schedule2SetpointHeat'])
            self.globals['schedules'][devId]['running'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 2, True)
            self.globals['schedules'][devId]['running'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 2, False)

        if self.globals['trvc'][devId]['schedule3Enabled']:
            scheduleTimeOnUi = self.globals['trvc'][devId]['schedule3TimeOn']
            scheduleTimeOn = int(scheduleTimeOnUi.replace(':','')) * 100  # Add in Seconds
            scheduleTimeOffUi =self.globals['trvc'][devId]['schedule3TimeOff']
            scheduleTimeOff = int(scheduleTimeOffUi.replace(':','')) * 100  # Add in Seconds
            scheduleSetpointOn = float(self.globals['trvc'][devId]['schedule3SetpointHeat'])
            self.globals['schedules'][devId]['running'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 3, True)
            self.globals['schedules'][devId]['running'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 3, False)

        if self.globals['trvc'][devId]['schedule4Enabled']:
            scheduleTimeOnUi = self.globals['trvc'][devId]['schedule4TimeOn']
            scheduleTimeOn = int(scheduleTimeOnUi.replace(':','')) * 100  # Add in Seconds
            scheduleTimeOffUi =self.globals['trvc'][devId]['schedule4TimeOff']
            scheduleTimeOff = int(scheduleTimeOffUi.replace(':','')) * 100  # Add in Seconds
            scheduleSetpointOn = float(self.globals['trvc'][devId]['schedule4SetpointHeat'])
            self.globals['schedules'][devId]['running'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 4, True)
            self.globals['schedules'][devId]['running'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 4, False)

        self.globals['schedules'][devId]['running'] = collections.OrderedDict(sorted(self.globals['schedules'][devId]['running'].items()))
        self.globals['schedules'][devId]['dynamic'] = self.globals['schedules'][devId]['running'].copy()

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_DELAY_COMMAND, devId, [CMD_PROCESS_HEATING_SCHEDULE, 2.0, None]])            

    def processShowSchedule(self, pluginAction, trvcDev):

        try:
            self.methodTracer.threaddebug(u'Main Plugin Method')

            scheduleReportLineLength = 80
            scheduleReport = u'\n{}'.format('=' * scheduleReportLineLength)
            scheduleReport = scheduleReport + self.boxLine('TRV Controller Plugin - Heating Schedule', scheduleReportLineLength, u'==')
            scheduleReport = scheduleReport + self.boxLine(' ', scheduleReportLineLength, u'==')
            scheduleReport = scheduleReport + self._showSchedule(trvcDev.id, scheduleReportLineLength)
            scheduleReport = scheduleReport + self.boxLine(' ', scheduleReportLineLength, u'==')
            scheduleReport = scheduleReport + u'\n{}\n'.format('=' * scheduleReportLineLength)

            self.generalLogger.info(scheduleReport)

        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [processShowSchedule]. Line \'{}\' has error=\'{}\''.format(indigo.devices[devId].name, sys.exc_traceback.tb_lineno, err))   

    def processShowAllSchedules(self, pluginAction):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        scheduleReportLineLength = 80
        scheduleReport = u'\n{}'.format('=' * scheduleReportLineLength)
        scheduleReport = scheduleReport + self.boxLine('TRV Controller Plugin - Heating Schedules', scheduleReportLineLength, u'==')
        scheduleReport = scheduleReport + self.boxLine(' ', scheduleReportLineLength, u'==')

        for trvcDev in indigo.devices.iter("self"):
            scheduleReport = scheduleReport + self._showSchedule(trvcDev.id, scheduleReportLineLength)

        scheduleReport = scheduleReport + self.boxLine(' ', scheduleReportLineLength, u'==')
        scheduleReport = scheduleReport + u'\n{}\n'.format('=' * scheduleReportLineLength)

        self.generalLogger.info(scheduleReport)

    def _showSchedule(self, trvCtlrDevId, scheduleReportLineLength):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        scheduleReport = ''

        trvcDev = indigo.devices[trvCtlrDevId]

        if trvcDev.enabled and trvcDev.configured:
            trvCtlrDevId = trvcDev.id
            scheduleReport = scheduleReport + self.boxLine(u'Device: \'{}\''.format(trvcDev.name), scheduleReportLineLength, u'==')
            scheduleList =  collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['dynamic'].items()))

            ScheduleGroupList = []
            ScheduleGroupList.append((collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['default'].items())),'Default'))
            ScheduleGroupList.append((collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['running'].items())), 'Running'))
            ScheduleGroupList.append((collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['dynamic'].items())), 'Dynamic'))

            storedScheduleDefault = {}
            storedScheduleRunnning = {}

            for scheduleList, scheduleType in ScheduleGroupList:
                if (scheduleType == 'Default' or scheduleType == 'Dynamic') and len(scheduleList) == 2:
                    continue
                elif scheduleType == 'Running' and len(scheduleList) == 2:
                    scheduleReport = scheduleReport + self.boxLine(u'  No schedules defined or enabled for device.', scheduleReportLineLength, u'==')
                    continue
                else:
                    scheduleReport = scheduleReport + self.boxLine(u'  Schedule Type: \'{}\''.format(scheduleType), scheduleReportLineLength, u'==')

                previousScheduleId = 0
                for key, value in scheduleList.items():
                    # scheduleTime = int(key)
                    scheduleTimeUi = u'{}'.format(value[0]) 
                    scheduleSetpoint = float(value[1])
                    scheduleId = value[2]
                    if scheduleId == 0:  # Ignore start entry (00:00)
                        continue
                    if previousScheduleId == 0 or previousScheduleId != scheduleId:
                        previousScheduleId = scheduleId
                        previousScheduleTimeUi = scheduleTimeUi
                        previousScheduleSetpoint = scheduleSetpoint
                    else:
                        scheduleEnabledName = 'schedule{}Enabled'.format(previousScheduleId)
                        scheduleActivedName = 'schedule{}Active'.format(previousScheduleId)

                         # self.generalLogger.info(u'scheduleActivedName = {}, {}'.format(scheduleActivedName, self.globals['trvc'][trvCtlrDevId][scheduleActivedName], self.globals['trvc'][trvCtlrDevId][scheduleActivedName]))


                        if self.globals['trvc'][trvCtlrDevId][scheduleEnabledName]:
                            combinedScheduleTimesUi = '{} - {}'.format(previousScheduleTimeUi, scheduleTimeUi) 
                            scheduleUi = 'Schedule {}: {}. Setpoint = {}'.format(scheduleId, combinedScheduleTimesUi, previousScheduleSetpoint)
                            # schedule = self.globals['trvc'][trvCtlrDevId]['schedule1TimeOn'] + ' - ' + self.globals['trvc'][trvCtlrDevId]['schedule1TimeOff']
                        else:
                            scheduleUi = 'Schedule {}: Disabled'.format(scheduleId)

                        if scheduleType == 'Default':
                            storedScheduleDefault[scheduleId] = scheduleUi
                        elif scheduleType == 'Running':
                            storedScheduleRunnning[scheduleId] = scheduleUi
                            if storedScheduleDefault[scheduleId] != storedScheduleRunnning[scheduleId]:
                                scheduleUi = '{} [*]'.format(scheduleUi)
                        elif scheduleType == 'Dynamic':
                            if storedScheduleRunnning[scheduleId] != scheduleUi:
                                scheduleUi = '{} [*]'.format(scheduleUi)
                            if trvcDev.states[scheduleActivedName]:
                                scheduleUi = '{} ACTIVE'.format(scheduleUi)

                        scheduleReport = scheduleReport + self.boxLine(u'    {}'.format(scheduleUi), scheduleReportLineLength, u'==')
        
        return scheduleReport

    def boxLine(self, info, lineLength, boxCharacters):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        fillLength = lineLength - len(info) - 1 - (2 * len(boxCharacters))
        if fillLength < 0:
            return boxCharacters + u'\n LINE LENGTH {} TOO SMALL FOR BOX CHARACTERS \'{}\' AND INFORMATION \'{}\''.format(lineLength, boxCharacters, info)

        lenBoxCharacters = len(boxCharacters)
        updatedLine = u'\n{} {}{}{}'.format(boxCharacters, info, (' ' * fillLength), boxCharacters )
        return updatedLine


    def processUpdateAllCsvFiles(self, pluginAction, trvCtlrDev):
 
        try:
            self.methodTracer.threaddebug(u'Main Plugin Method')

            trvCtlrDevId = trvCtlrDev.id
            if self.globals['config']['csvStandardEnabled']:
                if self.globals['trvc'][trvCtlrDevId]['updateCsvFile']:
                    self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES, trvCtlrDevId, None])
                else:
                    self.generalLogger.error(u'Update All CSV Files request ignored as option \'On State Change [...]\' not set for \'{}\' in its device settings.'.format(trvCtlrDev.name)) 
            else:
                self.generalLogger.error(u'Update All CSV Files request ignored for \'{}\' as option \'Enable Standard CSV\' not enabled in the plugin config.'.format(trvCtlrDev.name, trvCtlrDev.name))   


        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [processUpdateCsvFiles]. Line \'{}\' has error=\'{}\''.format(indigo.devices[devId].name, sys.exc_traceback.tb_lineno, err))   

    def processUpdateAllCsvFilesViaPostgreSQL(self, pluginAction, trvCtlrDev):
 
        try:
            self.methodTracer.threaddebug(u'Main Plugin Method')

            overrideDefaultRetentionHours = pluginAction.props.get('overrideDefaultRetentionHours', '')
            if overrideDefaultRetentionHours == '':
                overrideDefaultRetentionHours = 0
            else:
                overrideDefaultRetentionHours = int(overrideDefaultRetentionHours)
            overrideCsvFilePrefix = pluginAction.props.get('overrideCsvFilePrefix', '')

            trvCtlrDevId = trvCtlrDev.id
            if self.globals['config']['csvPostgresqlEnabled']:
                if self.globals['trvc'][trvCtlrDevId]['updateAllCsvFilesViaPostgreSQL']:
                    self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES_VIA_POSTGRESQL, trvCtlrDevId, [overrideDefaultRetentionHours, overrideCsvFilePrefix]])
                else:
                    self.generalLogger.error(u'Update All CSV Files Via PostgreSQL request ignored as option \'Enable PostgreSQL CSV\' not set for \'{}\' in its device settings.'.format(trvCtlrDev.name))   
            else:
                self.generalLogger.error(u'Update All CSV Files Via PostgreSQL request ignored for \'{}\' as option \'Enable PostgreSQL CSV\' not enabled in the plugin config.'.format(trvCtlrDev.name, trvCtlrDev.name))   

        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [processUpdateAllCsvFilesViaPostgreSQL]. Line \'{}\' has error=\'{}\''.format(indigo.devices[devId].name, sys.exc_traceback.tb_lineno, err))   

    def processShowStatus(self, pluginAction, dev):
 
        self.methodTracer.threaddebug(u'Main Plugin Method')

        devId = dev.id
        self.generalLogger.info('Showing full internal status of \'{}\''.format(dev.name))
        for self.key in sorted(self.globals['trvc'][devId].iterkeys()):
            self.generalLogger.info('\'{}\': {} = {}'.format(dev.name, self.key, self.globals['trvc'][devId][self.key]))
        self.generalLogger.info("Showing Heat SourceTRV Controller Device Table")
        for dev in self.globals['devicesToTrvControllerTable'].iteritems():
            self.generalLogger.info("Device: {}".format(dev))


    def processShowZwaveWakeupInterval(self, pluginAction):
 
        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.statusOptimize = dict()
        for dev in indigo.devices.iter("self"):
            if dev.enabled and dev.configured:
                devId = dev.id

                if self.globals['trvc'][devId]['zwaveDeltaCurrent'] != "[n/a]":
                    self.tempSplit = self.globals['trvc'][devId]['zwaveDeltaCurrent'].split(':')
                    self.tempZwaveDeltaCurrent = int(self.tempSplit[0]) * 60 + int(self.tempSplit[1])
                    # self.tempZwaveDeltaCurrent = datetime.datetime.strptime(self.globals['trvc'][devId]['zwaveDeltaCurrent'], '%M:%S')
                    self.tempA, self.tempB = divmod(self.tempZwaveDeltaCurrent, 300)
                    self.statusOptimize[dev.name] = int(self.tempB)

        self.generalLogger.info(u"Z-wave wakeup intervals between TRVs (in seconds):")
        self.optimizeDifference = 0
        self.sorted = sorted(self.statusOptimize.iteritems(), key=operator.itemgetter(1,0))
        for item1 in self.sorted:
            if self.optimizeDifference == 0:  # Ensure Intervals start at zero
                self.optimizeDifference = int(item1[1])
            self.optimizeDifferenceCalc = int(item1[1] - self.optimizeDifference)
            self.generalLogger.info("  %s = %s [Interval = %s]" % (item1[0], str("  " + str(item1[1]))[-3:], str("  " + str(self.optimizeDifferenceCalc))[-3:]))
            self.optimizeDifference = int(item1[1])

    def getDeviceConfigUiValues(self, pluginProps, typeId, devId):

        try:
            self.methodTracer.threaddebug(u'Main Plugin Method')

            if not 'remoteDeltaMax' in pluginProps:
                pluginProps['remoteDeltaMax'] = pluginProps.get('remoteTRVDeltaMax', '5.0')  # This is a fix to transfer the old name value (remoteTRVDeltaMax) to the new name value (remoteDeltaMax)
            # if not 'trvDeltaMax' in pluginProps:
            #      pluginProps['trvDeltaMax'] = '0.0'
            if not 'heatingId' in pluginProps:
                 pluginProps['heatingId'] = '-1'
            if not 'heatingVarId' in pluginProps:
                 pluginProps['heatingVarId'] = '-1'
            if 'forceTrvOnOff' in pluginProps:
                pluginProps['enableTrvOnOff'] = pluginProps['forceTrvOnOff']
                del pluginProps['forceTrvOnOff']

        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [validateSchedule] for device \'{}\'. Line \'{}\' has error=\'{}\''.format(indigo.devices[devId].name, sys.exc_traceback.tb_lineno, err))   

        return super(Plugin, self).getDeviceConfigUiValues(pluginProps, typeId, devId)


    def validateDeviceConfigUi(self, valuesDict, typeId, devId):  # Validate TRV Thermostat Controller

        try:
            self.methodTracer.threaddebug(u'Main Plugin Method')

            # Validate TRV Device
            trvDevId = 0
            valid = False
            try:
                trvDevId = int(valuesDict.get('trvDevId', 0))
                if trvDevId != 0 and valuesDict['supportedModel'] != 'Unknown TRV Model':
                    valid = True
            except:
                pass
            if not valid: 
                try:
                    model = 'a \'{}\' is not a TRV known by the plugin.'.format(indigo.devices[trvDevId].model)
                except KeyError:
                    model = 'no device selected!'
                errorDict = indigo.Dict()
                errorDict['trvDevId'] = 'Select a known TRV device'
                errorDict['showAlertText'] = 'You must select a TRV device  to monitor which is known by the plugin; {}'.format(model)
                return (False, valuesDict, errorDict)

            self.trvThermostatDeviceSelected(valuesDict, typeId, devId)

            # # Validate TRV Delta Maximum
            # trvDeltaMax = float(valuesDict.get('trvDeltaMax', 0.0))
            # if trvDeltaMax < 0.0 or trvDeltaMax > 10.0 or trvDeltaMax % 0.5 != 0:
            #     errorDict = indigo.Dict()
            #     errorDict['trvDeltaMax'] = 'TRV Delta Max must be set between 0.0 and 10.0 (inclusive)'
            #     errorDict['showAlertText'] = 'You must enter a valid maximum number of degrees to exceed the TRV Heat Setpoint by. It must be set between 0.0 and 10.0 (inclusive) and a multiple of 0.5.'
            #     return (False, valuesDict, errorDict)

            # Validate Device Heat Source Controller
            heatingId = -1
            valid = False
            try:
                heatingId  = int(valuesDict.get('heatingId', -1))
                if heatingId != -1:
                    if heatingId == 0:
                        valid = True
                    else:
                        if self.globals['config']['disableHeatSourceDeviceListFilter']:
                            valid = True
                        else:
                            model = indigo.devices[heatingId].model
                            if model in self.globals['supportedHeatSourceControllers']:
                                valid = True
                            else:
                                heatingId = 0
            except:
                pass
            if not valid:
                errorDict = indigo.Dict()
                errorDict['heatingId'] = 'Select a Heat Source Controller device or Not Required'
                errorDict['showAlertText'] = 'You must select a Heat Source Controller to switch on heat for the TRV or specify Not Required.'
                return (False, valuesDict, errorDict)

            # Validate Variable Heat Source Controller
            heatingVarId = -1
            valid = False
            try:
                heatingVarId  = int(valuesDict.get('heatingVarId', -1))
                if heatingVarId != -1:
                    valid = True
            except:
                pass
            if not valid:
                errorDict = indigo.Dict()
                errorDict['heatingVarId'] = 'Select a Heat Source Controller variable or Not Required'
                errorDict['showAlertText'] = 'You must select a Heat Source Controller to switch on heat for the TRV or specify Not Required.'
                return (False, valuesDict, errorDict)

            # Check whether to validate Remote Thermostat
            remoteDevId = 0
            valid = False
            remoteThermostatContolEnabled = bool(valuesDict.get('remoteThermostatContolEnabled', False))
            if remoteThermostatContolEnabled:
                try:
                    remoteDevId = int(valuesDict.get('remoteDevId', 0))
                    if remoteDevId != 0 and indigo.devices[remoteDevId].deviceTypeId != 'trvController':
                        remoteDev = indigo.devices[remoteDevId] 
                        if (remoteDev.subModel == 'Temperature'
                            or remoteDev.subModel == 'Temperature 1'
                            or remoteDev.subModel == 'Thermostat'
                            or remoteDev.subModel[0:7].lower() == 'sensor '
                            or 'temperatureInput1' in remoteDev.states
                            or 'temperature' in remoteDev.states
                            or 'Temperature' in remoteDev.states
                            or remoteDev.deviceTypeId == 'hueMotionTemperatureSensor'):
                            valid = True
                    else:
                        remoteDevId = 0
                except:
                    pass
                if not valid: 
                    try:
                        model = 'a \'{}\' is not a Remote Thermostat understood by this plugin.'.format(indigo.devices[remoteDevId].model)
                    except KeyError:
                        model = 'no device selected!'
                    errorDict = indigo.Dict()
                    errorDict['remoteDevId'] = 'Select a Remote Thermostat device'
                    errorDict['showAlertText'] = 'You must select a Remote thermostat to control the TRV; {}'.format(model)
                    return (False, valuesDict, errorDict)


                if remoteDevId != 0:

                    # Validate Remote Delta Maximum
                    valid = False
                    try:
                        remoteDeltaMax = float(valuesDict.get('remoteDeltaMax', 5.0))
                        valid = True
                    except:
                        pass
                    if not valid or remoteDeltaMax < 0.0 or remoteDeltaMax > 10.0 or remoteDeltaMax % 0.5 != 0:
                        errorDict = indigo.Dict()
                        errorDict['remoteDeltaMax'] = 'Remote Delta Max must be set between 0.0 and 10.0 (inclusive)'
                        errorDict['showAlertText'] = 'You must enter a valid maximum number of degrees to exceed the TRV Heat Setpoint for the remote thermostat. It must be set between 0.0 and 10.0 (inclusive) and a multiple of 0.5.'
                        return (False, valuesDict, errorDict)

                    # Validate Remote Temperature Offset
                    valid = False
                    try:
                        remoteTempOffset = float(valuesDict.get('remoteTempOffset', 0.0))
                        valid = True
                    except:
                        pass
                    if not valid or remoteTempOffset < -5.0 or remoteDeltaMax > 5.0:
                        errorDict = indigo.Dict()
                        errorDict['remoteTempOffset'] = 'Remote Temperature Offset must be set between -5.0 and 5.0 (inclusive)'
                        errorDict['showAlertText'] = 'You must enter a valid Remote Temperature Offset. It must be set between -5.0 and 5.0 (inclusive).'
                        return (False, valuesDict, errorDict)

            # Validate CSV Fields

            csvCreationMethod = int(valuesDict.get('csvCreationMethod', 0))

            if csvCreationMethod == '1' or csvCreationMethod == '2':
                csvShortName = valuesDict.get('csvShortName', '')
                if len(csvShortName) < 1 or len(csvShortName) > 10:
                    errorDict = indigo.Dict()
                    errorDict['csvShortName'] = 'Short Name must be present and have a length between 1 and 10 (inclusive).'
                    errorDict['showAlertText'] = 'Short Name must be present and have a length between 1 and 10 (inclusive).'
                    return (False, valuesDict, errorDict)
                valid = False
                try:
                    csvRetentionPeriodHours = int(valuesDict.get('csvRetentionPeriodHours', 24))
                    if csvRetentionPeriodHours > 0:
                        valid = True
                except:
                    pass
                if not valid:
                    errorDict = indigo.Dict()
                    errorDict['csvRetentionPeriodHours'] = 'Retention Period (Hours) must be a positive integer.'
                    errorDict['showAlertText'] = 'Retention Period (Hours) must be a positive integer.'
                    return (False, valuesDict, errorDict)


            # Validate Polling Fields

            supportsWakeup = valuesDict.get('supportsWakeup', 'true')

            # indigo.server.log(u'SUPPORTS WAKEUP [{}]: {}'.format(type(supportsWakeup), supportsWakeup))

            if supportsWakeup == 'false':
                valid = False
                try:
                    pollingScheduleActive = int(valuesDict.get('pollingScheduleActive', 5))
                    if pollingScheduleActive >= 0:
                        valid = True 
                except:
                    pass
                if not valid:
                    errorDict = indigo.Dict()
                    errorDict['pollingScheduleActive'] = 'Polling Minutes [Schedule Active] must be a positive integer or zero to disable.'
                    errorDict['showAlertText'] = 'Polling Minutes [Schedule Active] must be a positive integer or zero to disable.'
                    return (False, valuesDict, errorDict)

                valid = False
                try:
                    pollingScheduleInactive = int(valuesDict.get('pollingScheduleInactive', 5))
                    if pollingScheduleInactive >= 0:
                        valid = True 
                except:
                    pass
                if not valid:
                    errorDict = indigo.Dict()
                    errorDict['pollingScheduleInactive'] = 'Polling Minutes [Schedule Inactive] must be a positive integer or zero to disable.'
                    errorDict['showAlertText'] = 'Polling Minutes [Schedule Inactive] must be a positive integer or zero to disable.'
                    return (False, valuesDict, errorDict)

                valid = False
                try:
                    pollingSchedulesNotEnabled = int(valuesDict.get('pollingSchedulesNotEnabled', 5))
                    if pollingSchedulesNotEnabled >= 0:
                        valid = True 
                except:
                    pass
                if not valid:
                    errorDict = indigo.Dict()
                    errorDict['pollingSchedulesNotEnabled'] = 'Polling Minutes [Schedules Not Enabled] must be a positive integer or zero to disable.'
                    errorDict['showAlertText'] = 'Polling Minutes [Schedules Not Enabled] must be a positive integer or zero to disable.'
                    return (False, valuesDict, errorDict)


                valid = False
                try:
                    pollingScheduleInactive = int(valuesDict.get('pollingScheduleInactive', 5))
                    if pollingScheduleInactive >= 0:
                        valid = True 
                except:
                    pass

            # Validate Device Start Method fields
            setpointHeatDeviceStartMethod = int(valuesDict.get('setpointHeatDeviceStartMethod', DEVICE_START_SETPOINT_DEVICE_MINIMUM))
            if setpointHeatDeviceStartMethod == DEVICE_START_SETPOINT_SPECIFIED:
                valid = False
                try:
                    setpointHeatDeviceStartDefault = float(valuesDict.get('setpointHeatDeviceStartDefault', 8.0))
                    if setpointHeatDeviceStartDefault >= 8 and setpointHeatDeviceStartDefault <= 30 and setpointHeatDeviceStartDefault % 0.5 == 0.0:
                        valid = True 
                except:
                    pass
                if not valid:
                    errorDict = indigo.Dict()
                    errorDict['setpointHeatDeviceStartDefault'] = 'Temperature must be set between 8 and 30 (inclusive)'
                    errorDict['showAlertText'] = 'You must enter a valid \'Device Start\' temperature for the TRV. It must be set between 8 and 30 (inclusive) and a multiple of 0.5.'
                    return (False, valuesDict, errorDict)

            # Validate default ON temperature
            valid = False
            try:
                setpointHeatOnDefault = float(valuesDict.get('setpointHeatOnDefault', 0))
                if setpointHeatOnDefault >= 10.0 and setpointHeatOnDefault <= 30.0 and setpointHeatOnDefault % 0.5 == 0.0:
                    valid = True 
            except:
                pass
            if not valid:
                errorDict = indigo.Dict()
                errorDict['setpointHeatOnDefault'] = 'Temperature must be set between 10 and 30 (inclusive)'
                errorDict['showAlertText'] = 'You must enter a valid Turn On temperature for the TRV. It must be set between 10 and 30 (inclusive) and a multiple of 0.5.'
                return (False, valuesDict, errorDict)

            # Validate Schedule 1
            schedule1Enabled = bool(valuesDict.get('schedule1Enabled', False))
            if schedule1Enabled:
                scheduleValid, scheduleData = self.validateSchedule(devId, valuesDict, '1')
                if not scheduleValid:
                    return (False, valuesDict, scheduleData)  # i.e. False, valuesDict, errorDict
                schedule1TimeOn = scheduleData[0]
                schedule1TimeOff = scheduleData[1]
                schedule1SetpointHeat = scheduleData[2]

            # Validate Schedule 2
            schedule2Enabled = bool(valuesDict.get('schedule2Enabled', False))
            if schedule2Enabled:
                scheduleValid, scheduleData = self.validateSchedule(devId, valuesDict, '2')
                if not scheduleValid:
                    return (False, valuesDict, scheduleData)  # i.e. False, valuesDict, errorDict
                schedule2TimeOn = scheduleData[0]
                schedule2TimeOff = scheduleData[1]
                schedule2SetpointHeat = scheduleData[2]

            # Validate Schedule 3
            schedule3Enabled = bool(valuesDict.get('schedule3Enabled', False))
            if schedule3Enabled:
                scheduleValid, scheduleData = self.validateSchedule(devId, valuesDict, '3')
                if not scheduleValid:
                    return (False, valuesDict, scheduleData)  # i.e. False, valuesDict, errorDict
                schedule3TimeOn = scheduleData[0]
                schedule3TimeOff = scheduleData[1]
                schedule3SetpointHeat = scheduleData[2]

            # Validate Schedule 3
            schedule4Enabled = bool(valuesDict.get('schedule4Enabled', False))
            if schedule4Enabled:
                scheduleValid, scheduleData = self.validateSchedule(devId, valuesDict, '4')
                if not scheduleValid:
                    return (False, valuesDict, scheduleData)  # i.e. False, valuesDict, errorDict
                schedule4TimeOn = scheduleData[0]
                schedule4TimeOff = scheduleData[1]
                schedule4SetpointHeat = scheduleData[2]

            # Consistency check across schedules
            if schedule1Enabled:
                if schedule2Enabled:
                    if schedule1TimeOff < schedule2TimeOn:
                        secondsDelta = secondsFromHHMM(schedule2TimeOn) - secondsFromHHMM(schedule1TimeOff)
                    else:
                        secondsDelta = 0
                    if secondsDelta < 600:  # 10 minutes (600 seconds) check
                        errorDict = indigo.Dict()
                        errorDict['schedule1TimeOff'] = 'The Schedule One heating OFF time must end before the Schedule Two heating ON time'
                        errorDict['schedule2TimeOn'] = 'The Schedule Two heating On time must start after the Schedule One heating Off time'
                        errorDict['showAlertText'] = 'The Schedule One OFF time [{}] must be before the Schedule Two ON time [{}] and there must be at least 10 minutes between the Schedule One OFF time and Schedule Two ON time.'.format(schedule1TimeOff, schedule2TimeOn)
                        return (False, valuesDict, errorDict)
                if schedule3Enabled:
                    if schedule1TimeOff < schedule3TimeOn:
                        secondsDelta = secondsFromHHMM(schedule3TimeOn) - secondsFromHHMM(schedule1TimeOff)
                    else:
                        secondsDelta = 0
                    if secondsDelta < 600:  # 10 minutes (600 seconds) check
                        errorDict = indigo.Dict()
                        errorDict['schedule1TimeOff'] = 'The Schedule One heating OFF time must end before the Schedule Three heating ON time'
                        errorDict['schedule3TimeOn'] = 'The Schedule Three heating On time must start after the Schedule One heating Off time'
                        errorDict['showAlertText'] = 'The Schedule One OFF time [{}] must be before the Schedule Three ON time [{}] and there must be at least 10 minutes between the Schedule One OFF time and Schedule Three ON time.'.format(schedule1TimeOff, schedule3TimeOn)
                        return (False, valuesDict, errorDict)
                if schedule4Enabled:
                    if schedule1TimeOff < schedule4TimeOn:
                        secondsDelta = secondsFromHHMM(schedule4TimeOn) - secondsFromHHMM(schedule1TimeOff)
                    else:
                        secondsDelta = 0
                    if secondsDelta < 600:  # 10 minutes (600 seconds) check
                        errorDict = indigo.Dict()
                        errorDict['schedule1TimeOff'] = 'The Schedule One heating OFF time must end before the Schedule Four heating ON time'
                        errorDict['schedule4TimeOn'] = 'The Schedule Four heating On time must start after the Schedule One heating Off time'
                        errorDict['showAlertText'] = 'The Schedule One OFF time [{}] must be before the Schedule Four ON time [{}] and there must be at least 10 minutes between the Schedule One OFF time and Schedule Four ON time.'.format(schedule1TimeOff, schedule3TimeOn)
                        return (False, valuesDict, errorDict)
            if schedule2Enabled:
                if schedule3Enabled:
                    if schedule2TimeOff < schedule3TimeOn:
                        secondsDelta = secondsFromHHMM(schedule3TimeOn) - secondsFromHHMM(schedule2TimeOff)
                    else:
                        secondsDelta = 0
                    if secondsDelta < 600:  # 10 minutes (600 seconds) check
                        errorDict = indigo.Dict()
                        errorDict['schedule2TimeOff'] = 'The Schedule Two heating OFF time must end before the Schedule Three heating ON time'
                        errorDict['schedule3TimeOn'] = 'The Schedule Three heating On time must start after the Schedule Two heating Off time'
                        errorDict['showAlertText'] = 'The Schedule Two OFF time [{}] must be before the Schedule Three ON time [{}] and there must be at least 10 minutes between the Schedule Two OFF time and Schedule Three ON time.'.format(schedule2TimeOff, schedule3TimeOn)
                        return (False, valuesDict, errorDict)
                if schedule4Enabled:
                    if schedule2TimeOff < schedule4TimeOn:
                        secondsDelta = secondsFromHHMM(schedule4TimeOn) - secondsFromHHMM(schedule2TimeOff)
                    else:
                        secondsDelta = 0
                    if secondsDelta < 600:  # 10 minutes (600 seconds) check
                        errorDict = indigo.Dict()
                        errorDict['schedule2TimeOff'] = 'The Schedule Two heating OFF time must end before the Schedule Four heating ON time'
                        errorDict['schedule4TimeOn'] = 'The Schedule Four heating On time must start after the Schedule Two heating Off time'
                        errorDict['showAlertText'] = 'The Schedule Two OFF time [{}] must be before the Schedule Four ON time [{}] and there must be at least 10 minutes between the Schedule Two OFF time and Schedule Four ON time.'.format(schedule2TimeOff, schedule4TimeOn)
                        return (False, valuesDict, errorDict)

            if schedule3Enabled:
                if schedule4Enabled:
                    if schedule3TimeOff < schedule4TimeOn:
                        secondsDelta = secondsFromHHMM(schedule4TimeOn) - secondsFromHHMM(schedule3TimeOff)
                    else:
                        secondsDelta = 0
                    if secondsDelta < 600:  # 10 minutes (600 seconds) check
                        errorDict = indigo.Dict()
                        errorDict['schedule3TimeOff'] = 'The Schedule Three heating OFF time must end before the Schedule Four heating ON time'
                        errorDict['schedule4TimeOn'] = 'The Schedule Four heating On time must start after the Schedule Three heating Off time'
                        errorDict['showAlertText'] = 'The Schedule Three OFF time [{}] must be before the Schedule Four ON time [{}] and there must be at least 10 minutes between the Schedule Three OFF time and Schedule Four ON time.'.format(schedule3TimeOff, schedule4TimeOn)
                        return (False, valuesDict, errorDict)

            return (True, valuesDict)

        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [validateDeviceConfigUi]. Line \'{}\' has error=\'{}\''.format(indigo.devices[devId].name, sys.exc_traceback.tb_lineno, err))   

    def heatSourceControllerDevices(self, filter="", valuesDict=None, typeId="", targetId=0):
        
        self.methodTracer.threaddebug(u'Main Plugin Method')

        myArray = []
        for dev in indigo.devices:
            if self.globals['config']['disableHeatSourceDeviceListFilter']:
                try:
                    if dev.deviceTypeId == 'zwThermostatType' or dev.deviceTypeId == 'zwRelayType' or dev.deviceTypeId == 'pseudoRelay':
                        if dev.model not in self.globals['supportedTrvModels']:
                            myArray.append((dev.id, dev.name))
                    # if dev.model not in self.globals['supportedTrvModels']:
                    #     myArray.append((dev.id, dev.name))
                except:
                    pass
            else:
                if dev.model in self.globals['supportedHeatSourceControllers']:
                    myArray.append((dev.id, dev.name))

        myArraySorted = sorted(myArray, key=lambda devname: devname[1].lower())   # sort by device name
        myArraySorted.insert(0, (0, 'NO HEAT SOURCE DEVICE '))
        myArraySorted.insert(0, (-1, '-- Select Device Heat Source --'))

        return myArraySorted


    def heatSourceControllerVariables(self, filter="", valuesDict=None, typeId="", targetId=0):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        myArray = []
        for var in indigo.variables:
            if self.globals['config']['trvVariableFolderId'] == 0:
                myArray.append((var.id, var.name))
            else:
                if var.folderId == self.globals['config']['trvVariableFolderId']:
                    myArray.append((var.id, var.name))

        myArraySorted = sorted(myArray, key=lambda varname: varname[1].lower())   # sort by variable name
        myArraySorted.insert(0, (0, 'NO HEAT SOURCE VARIABLE'))
        myArraySorted.insert(0, (-1, '-- Select Variable Heat Source --'))

        return myArraySorted


    def trvControlledDevices(self, filter="", valuesDict=None, typeId="", targetId=0):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.myArray = []
        for dev in indigo.devices.iter("indigo.thermostat"):
            if dev.deviceTypeId != 'trvController':
                self.myArray.append((dev.id, dev.name))
        return sorted(self.myArray, key=lambda devname: devname[1].lower())   # sort by device name

    def trvThermostatDeviceSelected(self, valuesDict, typeId, devId):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        trvDevId = int(valuesDict.get('trvDevId', 0))
        if trvDevId != 0:
            trvDev = indigo.devices[trvDevId]
            trv_model_name = trvDev.model
            if trv_model_name in self.globals['supportedTrvModels']:
                pass
            else:
                trv_model_name = 'Unknown TRV Model'

            trvModelProperties = self.globals['supportedTrvModels'][trv_model_name]

            valuesDict['supportedModel'] = trv_model_name
            valuesDict['supportsWakeup'] = self.globals['supportedTrvModels'][trv_model_name]['supportsWakeup']
            valuesDict['supportsTemperatureReporting'] = self.globals['supportedTrvModels'][trv_model_name]['supportsTemperatureReporting']
            valuesDict['supportsHvacOnOff'] = self.globals['supportedTrvModels'][trv_model_name]['supportsHvacOnOff']
            valuesDict['supportsValveControl'] = self.globals['supportedTrvModels'][trv_model_name]['supportsValveControl']
            valuesDict['supportsManualSetpoint'] = self.globals['supportedTrvModels'][trv_model_name]['supportsManualSetpoint']
            valuesDict['setpointHeatMinimum'] = self.globals['supportedTrvModels'][trv_model_name]['setpointHeatMinimum']
            valuesDict['setpointHeatMaximum'] = self.globals['supportedTrvModels'][trv_model_name]['setpointHeatMaximum']

        return valuesDict

    def remoteThermostatDevices(self, filter="", valuesDict=None, typeId="", targetId=0):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        self.myArray = []
        for dev in indigo.devices.iter():
            if dev.deviceTypeId != 'trvController':
                if dev.subModel == 'Temperature' or dev.subModel == 'Temperature 1' or dev.subModel == 'Thermostat' or dev.deviceTypeId == 'hueMotionTemperatureSensor' or (dev.model == 'Thermostat (TF021)' and dev.subModel[0:7].lower() == 'sensor '):
                    self.myArray.append((dev.id, dev.name))
                else:
                    try:
                        test = float(dev.states['temperatureInput1'])  # e.g. Secure SRT321 / HRT4-ZW
                    except (AttributeError, KeyError, ValueError):
                        try:
                            test = float(dev.states['temperature'])  # e.g. Oregon Scientific Temp Sensor
                        except (AttributeError, KeyError, ValueError):
                            try:
                                test = float(dev.states['Temperature'])  # e.g. Netatmo
                            except (AttributeError, KeyError, ValueError):
                                # try:
                                #     test = float(dev.states['sensorValue'])  # e.g. HeatIT TF021
                                # except (AttributeError, KeyError, ValueError):
                                continue
                    self.myArray.append((dev.id, dev.name))

        return sorted(self.myArray, key=lambda devname: devname[1].lower())   # sort by device name


    def validateSchedule(self, trvcId, valuesDict, scheduleNumber):
        # Common routine to check a schedule: On time, off time and heat setpoint
        # Used by validateDeviceConfigUi

        try:
            self.methodTracer.threaddebug(u'Main Plugin Method')

            # setup names
            scheduleTimeOnName = 'schedule{}TimeOn'.format(scheduleNumber)
            scheduleTimeOffName = 'schedule{}TimeOff'.format(scheduleNumber)
            scheduleSetpointHeatName = 'schedule{}SetpointHeat'.format(scheduleNumber)

            # self.generalLogger.error(u'validateSchedule: OnName = \'{}\', OffName = \'{}\', SetpointHeatname = \'{}\''.format(scheduleTimeOnName, scheduleTimeOffName, scheduleSetpointHeatName))   

            scheduleName = ('One', 'Two', 'Three', 'Four')[int(scheduleNumber)-1]

            def validateTime(timeField):
                try:
                    if len(timeField) != 5:
                        return '24:00'
                    if timeField[2:3] != ':':
                        return '24:00'
                    hour = int(timeField[0:2])
                    if hour < 0 or hour > 23:
                        return '24:00'
                    minute = int(timeField[3:5]) 
                    if minute < 0 or minute > 59:
                        return '24:00'
                    # Valid at this point
                    return timeField
                except:
                    return '24:00'

            # Validate Schedule ON time
            scheduleTimeOn = '24:00'
            try:
                scheduleTimeToTest = valuesDict.get(scheduleTimeOnName, '24:00')
                scheduleTimeOn = validateTime(scheduleTimeToTest)
            except:
                pass
            if scheduleTimeOn == '00:00' or scheduleTimeOn == '24:00':
                errorDict = indigo.Dict()
                errorDict[scheduleTimeOnName] = 'Set time (in HH:MM format) between 00:01 and 23:59 (inclusive)'
                errorDict['showAlertText'] = 'You must enter a Schedule {} time (in HH:MM format) between 00:01 and 23:59 (inclusive) for when the TRV will turn ON.'.format(scheduleName)
                return (False, errorDict)

            # Validate Schedule OFF time
            scheduleTimeOff = '24:00'
            try:
                scheduleTimeToTest = valuesDict.get(scheduleTimeOffName, '24:00')
                scheduleTimeOff = validateTime(scheduleTimeToTest)
            except:
                pass
            if scheduleTimeOff == '00:00' or scheduleTimeOff == '24:00':
                errorDict = indigo.Dict()
                errorDict[scheduleTimeOffName] = 'Set time (in HH:MM format) between 00:01 and 23:59 (inclusive)'
                errorDict['showAlertText'] = 'You must enter a Schedule {} time (in HH:MM format) between 00:01 and 23:59 (inclusive) for when the TRV will turn OFF.'.format(scheduleName)
                return (False, errorDict)

            # Validate Schedule 1 Heat Setpoint

            setpointHeatMinimum = float(valuesDict.get('setpointHeatMinimum', 0.0))
            setpointHeatMaximum = float(valuesDict.get('setpointHeatMaximum', 0.0))

            if setpointHeatMinimum == 0.0 or setpointHeatMaximum == 0.0:
                errorDict = indigo.Dict()
                errorDict[scheduleSetpointHeatName] = 'TRV Maximum and Minimum Setpoint Heat Temperatures invalid - make sure to select TRV Thermostat Device before defining schedule'
                errorDict['showAlertText'] = 'TRV Maximum and Minimum Setpoint Heat Temperatures invalid for Schedule {}, make sure to select TRV Thermostat Device before defining schedule'.format(scheduleName)
                return (False, errorDict)

            valid = False
            try:
                scheduleSetpointHeat = float(valuesDict.get(scheduleSetpointHeatName, 0))
                valid = True
            except:
                pass

            if valid:  # so far!
                if scheduleSetpointHeat < setpointHeatMinimum or scheduleSetpointHeat > setpointHeatMaximum or scheduleSetpointHeat % 0.5 != 0:
                    valid = False

            if not valid:
                errorDict = indigo.Dict()
                errorDict[scheduleSetpointHeatName] = 'Setpoint temperature must be numeric and set between {} and {} (inclusive)'.format(setpointHeatMinimum, setpointHeatMaximum)
                errorDict['showAlertText'] = 'You must enter a valid Schedule {} Setpoint temperature for the TRV. It must be numeric and set between {} and {} (inclusive) and a multiple of 0.5.'.format(scheduleName, setpointHeatMinimum, setpointHeatMaximum)
                return (False, errorDict)

            # Check Schedule Times consistent
            if scheduleTimeOff > scheduleTimeOn:
                secondsDelta = secondsFromHHMM(scheduleTimeOff) - secondsFromHHMM(scheduleTimeOn)
            else:
                secondsDelta = 0
        
            if secondsDelta < 600:  # 10 minutes (600 seconds) check
                errorDict = indigo.Dict()
                errorDict[scheduleTimeOnName] = 'The Schedule {} ON time must be at least 10 minutes before the Schedule {} OFF time'.format(scheduleName, scheduleName)
                errorDict[scheduleTimeOffName] = 'The Schedule {} OFF time must be at least 10 minutes after the Schedule {} ON time'.format(scheduleName, scheduleName)
                errorDict['showAlertText'] = 'The Schedule {} ON time [{}] must be at least 10 minutes before the Schedule {} OFF time [{}]'.format(scheduleName, scheduleTimeOn, scheduleName, scheduleTimeOff)
                return (False, errorDict)

            return (True, [scheduleTimeOn, scheduleTimeOff, scheduleSetpointHeat])

        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [validateSchedule] for device \'{}\'. Line \'{}\' has error=\'{}\''.format(indigo.devices[trvcId].name, sys.exc_traceback.tb_lineno, err))   


    def closedDeviceConfigUi(self, valuesDict, userCancelled, typeId, trvCtlrDevId):

        try:
            self.methodTracer.threaddebug(u'Main Plugin Method')

            self.generalLogger.debug(u'\'closePrclosedDeviceConfigUiefsConfigUi\' called with userCancelled = {}'.format(str(userCancelled)))  

            if userCancelled:
                return

        except StandardError, e:
            self.generalLogger.error(u'closedDeviceConfigUi error detected. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, e))   
            return True

    def deviceStartComm(self, trvcDev):

        try:
            self.methodTracer.threaddebug(u'Main Plugin Method')

            trvCtlrDevId = trvcDev.id

            self.globals['trvc'][trvCtlrDevId] = dict()
            self.globals['trvc'][trvCtlrDevId]['deviceStarted'] = False

            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector'] = dict()
            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController'] = dict()
            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv'] = dict()
            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote'] = dict()

            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updateSecondsSinceMidnight'] = 0
            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updatesInLastSecond'] = 0
            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trvController']['updatesInLastSecondMaximum'] = 0
            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updateSecondsSinceMidnight'] = 0
            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updatesInLastSecond'] = 0
            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['trv']['updatesInLastSecondMaximum'] = 0
            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updateSecondsSinceMidnight'] = 0
            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updatesInLastSecond'] = 0
            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector']['remote']['updatesInLastSecondMaximum'] = 0

            if (trvcDev.pluginProps.get('version', '0.0')) !=  self.globals['pluginInfo']['pluginVersion']:
                self.pluginProps = trvcDev.pluginProps
                self.pluginProps["version"] = self.globals['pluginInfo']['pluginVersion']
                trvcDev.replacePluginPropsOnServer(self.pluginProps)

            self.currentTime = indigo.server.getTime()

            trvcDev.stateListOrDisplayStateIdChanged()  # Ensure latest devices.xml is being used

            self.globals['trvc'][trvCtlrDevId]['lastSuccessfulComm'] = 'N/A'
            self.globals['trvc'][trvCtlrDevId]['lastSuccessfulCommTrv'] = 'N/A'
            self.globals['trvc'][trvCtlrDevId]['lastSuccessfulCommRemote'] = 'N/A'
            self.globals['trvc'][trvCtlrDevId]['eventReceivedCountRemote'] = 0

            self.globals['trvc'][trvCtlrDevId]['hideTempBroadcast'] = bool(trvcDev.pluginProps.get('hideTempBroadcast', False))  # Hide Temperature Broadcast in Event Log Flag

            self.globals['trvc'][trvCtlrDevId]['trvDevId'] = int(trvcDev.pluginProps.get('trvDevId', 0))  # ID of TRV device
            # self.globals['trvc'][trvCtlrDevId]['trvDeltaMax'] = float(trvcDev.pluginProps.get('trvDeltaMax', 0.0))

            self.globals['trvc'][trvCtlrDevId]['valveDevId'] = 0
            self.globals['trvc'][trvCtlrDevId]['valvePercentageOpen'] = 0

            self.globals['trvc'][trvCtlrDevId]['csvCreationMethod'] = 0
            self.globals['trvc'][trvCtlrDevId]['csvStandardMode'] = 1
            self.globals['trvc'][trvCtlrDevId]['updateCsvFile'] = False
            self.globals['trvc'][trvCtlrDevId]['updateAllCsvFiles'] = False
            self.globals['trvc'][trvCtlrDevId]['updateAllCsvFilesViaPostgreSQL'] = False

            self.globals['trvc'][trvCtlrDevId]['csvCreationMethod'] = int(trvcDev.pluginProps.get('csvCreationMethod', 0))
            if self.globals['config']['csvStandardEnabled']:
                if self.globals['trvc'][trvCtlrDevId]['csvCreationMethod'] == 1:
                    self.globals['trvc'][trvCtlrDevId]['updateCsvFile'] = True
                    if self.globals['trvc'][trvCtlrDevId]['csvStandardMode'] == 2:
                        self.globals['trvc'][trvCtlrDevId]['updateAllCsvFiles'] = True
            if self.globals['config']['csvPostgresqlEnabled']:
                if self.globals['trvc'][trvCtlrDevId]['csvCreationMethod'] == 2:
                    self.globals['trvc'][trvCtlrDevId]['updateAllCsvFilesViaPostgreSQL'] = True
                    self.globals['trvc'][trvCtlrDevId]['postgresqlUser'] = self.globals['config']['postgresqlUser']
                    self.globals['trvc'][trvCtlrDevId]['postgresqlPassword'] = self.globals['config']['postgresqlPassword']
            self.globals['trvc'][trvCtlrDevId]['csvShortName'] = trvcDev.pluginProps.get('csvShortName', '')
            self.globals['trvc'][trvCtlrDevId]['csvRetentionPeriodHours'] = int(trvcDev.pluginProps.get('csvRetentionPeriodHours', 24))


            self.globals['trvc'][trvCtlrDevId]['pollingScheduleActive'] = float(int(trvcDev.pluginProps.get('pollingScheduleActive', 5)) * 60.0)
            self.globals['trvc'][trvCtlrDevId]['pollingScheduleInactive'] = float(int(trvcDev.pluginProps.get('pollingScheduleInactive', 20)) * 60.0)
            self.globals['trvc'][trvCtlrDevId]['pollingSchedulesNotEnabled'] = float(int(trvcDev.pluginProps.get('pollingSchedulesNotEnabled', 10)) * 60.0)
            self.globals['trvc'][trvCtlrDevId]['pollingSeconds'] = 0.0

            self.globals['trvc'][trvCtlrDevId]['valveAssistance'] = False
            self.globals['trvc'][trvCtlrDevId]['enableTrvOnOff'] = False
            if self.globals['trvc'][trvCtlrDevId]['trvDevId'] != 0:
                if trvcDev.address != indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address:
                    self.pluginProps = trvcDev.pluginProps
                    self.pluginProps["address"] = indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address
                    trvcDev.replacePluginPropsOnServer(self.pluginProps)

                self.globals['trvc'][trvCtlrDevId]['supportsHvacOnOff'] = bool(trvcDev.pluginProps.get('supportsHvacOnOff', False))
                if self.globals['trvc'][trvCtlrDevId]['supportsHvacOnOff']:
                    self.globals['trvc'][trvCtlrDevId]['enableTrvOnOff'] =  bool(trvcDev.pluginProps.get('enableTrvOnOff', False))
                self.globals['trvc'][trvCtlrDevId]['trvSupportsManualSetpoint'] =  bool(trvcDev.pluginProps.get('supportsManualSetpoint', False))
                self.globals['trvc'][trvCtlrDevId]['trvSupportsTemperatureReporting'] =  bool(trvcDev.pluginProps.get('supportsTemperatureReporting', False))
                self.generalLogger.debug(u'TRV SUPPORTS TEMPERATURE REPORTING: \'{}\' = {} '.format(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].name , self.globals['trvc'][trvCtlrDevId]['trvSupportsTemperatureReporting']))   

                self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address)] = dict() 
                self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address)]['devId'] = self.globals['trvc'][trvCtlrDevId]['trvDevId'] 
                self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address)]['type'] = TRV 
                self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address)]['trvcId'] = trvCtlrDevId
                self.globals['zwave']['WatchList'].add(int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address))

                for dev in indigo.devices:
                    if dev.address == trvcDev.address and dev.id != self.globals['trvc'][trvCtlrDevId]['trvDevId']:
                        if dev.model == 'Thermostat (Spirit)':
                            self.globals['trvc'][trvCtlrDevId]['valveAssistance'] = bool(trvcDev.pluginProps.get('valveAssistance', True))
                            self.globals['trvc'][trvCtlrDevId]['valveDevId'] = dev.id
                            self.globals['trvc'][trvCtlrDevId]['valvePercentageOpen'] = int(dev.states['brightnessLevel'])
                            valveAssistanceUi = ''
                            if self.globals['trvc'][trvCtlrDevId]['valveAssistance']:
                                valveAssistanceUi = '(Valve Assistance in Operation)' 
                            self.generalLogger.debug(u'Found Valve device for \'{}\': \'{}\' - Valve percentage open = {}% {}'.format(trvcDev.name, dev.name, self.globals['trvc'][trvCtlrDevId]['valvePercentageOpen'], valveAssistanceUi))
                            # self.generalLogger.debug(u'Found Valve device for \'{}\': \'{}\''.format(trvcDev.name, dev.name))
                            # self.globals['zwave']['WatchList'].add(int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['valveDevId']].address))  # Not needed as same address as Spirit TRV

            else: 
                # Work out how to handle this error situation !!!
                return



            self.globals['schedules'][trvCtlrDevId] = dict()
            self.globals['schedules'][trvCtlrDevId]['default'] = dict()  # setup from device configuration
            self.globals['schedules'][trvCtlrDevId]['running'] = dict()  # based on 'default' and potentially modified by change schedule actions
            self.globals['schedules'][trvCtlrDevId]['dynamic'] = dict()  # based on 'running' and potentially modified in response to Boost / Advance / Extend actions


            self.globals['trvc'][trvCtlrDevId]['remoteDevId'] = 0  # Assume no remote thermostat control
            self.globals['trvc'][trvCtlrDevId]['remoteThermostatContolEnabled'] = bool(trvcDev.pluginProps.get('remoteThermostatContolEnabled', False))
            if self.globals['trvc'][trvCtlrDevId]['remoteThermostatContolEnabled']:
                self.globals['trvc'][trvCtlrDevId]['remoteDevId'] = int(trvcDev.pluginProps.get('remoteDevId', 0))   # ID of Remote Thermostat device
                if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:

                    if indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].protocol == indigo.kProtocol.ZWave:

                        self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].address)] = dict()
                        self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].address)]['devId'] = self.globals['trvc'][trvCtlrDevId]['remoteDevId'] 
                        self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].address)]['type'] = REMOTE 
                        self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].address)]['trvcId'] = trvCtlrDevId
                        self.globals['zwave']['WatchList'].add(int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].address))

            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0 and self.globals['trvc'][trvCtlrDevId]['trvSupportsTemperatureReporting']:
                if trvcDev.pluginProps.get('NumTemperatureInputs', 0) != 2:
                    self.pluginProps = trvcDev.pluginProps
                    self.pluginProps["NumTemperatureInputs"] = 2
                    trvcDev.replacePluginPropsOnServer(self.pluginProps)
            else:
                if trvcDev.pluginProps.get('NumTemperatureInputs', 0) != 1:
                    self.pluginProps = trvcDev.pluginProps
                    self.pluginProps["NumTemperatureInputs"] = 1
                    trvcDev.replacePluginPropsOnServer(self.pluginProps)

            self.globals['trvc'][trvCtlrDevId]['trvSupportsHvacOperationMode'] = bool(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].supportsHvacOperationMode)
            self.generalLogger.debug(u'TRV \'{}\' supports HVAC Operation Mode = {}'.format(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].name, self.globals['trvc'][trvCtlrDevId]['trvSupportsHvacOperationMode']))

            self.globals['trvc'][trvCtlrDevId]['heatingId'] = int(trvcDev.pluginProps.get('heatingId', 0))  # ID of Heat Source Controller device

            if self.globals['trvc'][trvCtlrDevId]['heatingId'] != 0 and self.globals['trvc'][trvCtlrDevId]['heatingId'] not in self.globals['heaterDevices'].keys():
                self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']] = dict()
                self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['thermostatsCallingForHeat'] = set()  # SET of TRVs calling for heat from this heat source [None at the moment]

                self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['heaterControlType'] = HEAT_SOURCE_NOT_FOUND  # Default to No Heating Source
                try:
                    indigo.devices[self.globals['trvc'][trvCtlrDevId]['heatingId']].hvacMode
                    self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['heaterControlType'] = HEAT_SOURCE_CONTROL_HVAC  # hvac
                    self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['onState'] = HEAT_SOURCE_INITIALISE
                except AttributeError:
                    try:
                        indigo.devices[self.globals['trvc'][trvCtlrDevId]['heatingId']].onState
                        self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['heaterControlType'] = HEAT_SOURCE_CONTROL_RELAY  # relay device
                        self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['onState'] = HEAT_SOURCE_INITIALISE
                    except AttributeError:
                        indigo.server.error(u'Error detected by TRV Plugin for device [{}] - Unknown Heating Source Device Type with Id: {}'.format(trvcDev.name, self.globals['trvc'][trvCtlrDevId]['heatingId']))

                if not HEAT_SOURCE_NOT_FOUND:
                    self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_KEEP_HEAT_SOURCE_CONTROLLER_ALIVE, None, [self.globals['trvc'][trvCtlrDevId]['heatingId'], ]])                     

            self.globals['trvc'][trvCtlrDevId]['heatingVarId'] = int(trvcDev.pluginProps.get('heatingVarId', 0))  # ID of Heat Source Controller device

            if self.globals['trvc'][trvCtlrDevId]['heatingVarId'] != 0 and self.globals['trvc'][trvCtlrDevId]['heatingVarId'] not in self.globals['heaterVariables'].keys():
                self.globals['heaterVariables'][self.globals['trvc'][trvCtlrDevId]['heatingVarId']] = dict()
                self.globals['heaterVariables'][self.globals['trvc'][trvCtlrDevId]['heatingVarId']]['thermostatsCallingForHeat'] = set()  # SET of TRVs calling for heat from this heat source [None at the moment]
                indigo.variable.updateValue(self.globals['trvc'][trvCtlrDevId]['heatingVarId'], value="false")  # Variable indicator to show that heating is NOT being requested

            # Battery level setup
            self.globals['trvc'][trvCtlrDevId]['batteryLevel'] = 0
            self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'] = 0
            if self.globals['trvc'][trvCtlrDevId]['trvDevId'] != 0:
                if 'batteryLevel' in indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].states:
                    self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'] = indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].batteryLevel
            self.globals['trvc'][trvCtlrDevId]['batteryLevel'] = self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv']
            self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'] = 0   
            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:
                if 'batteryLevel' in indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].states:
                    self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'] = indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].batteryLevel
                    if 0 < self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'] < \
                            self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv']:
                        self.globals['trvc'][trvCtlrDevId]['batteryLevel'] = self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote']

            self.globals['trvc'][trvCtlrDevId]['setpointHeatOnDefault'] = float(trvcDev.pluginProps['setpointHeatOnDefault'])
            self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum'] = float(trvcDev.pluginProps['setpointHeatMinimum'])
            self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum'] = float(trvcDev.pluginProps['setpointHeatMaximum'])

            self.globals['trvc'][trvCtlrDevId]['setpointHeatDeviceStartMethod'] = int(trvcDev.pluginProps.get('setpointHeatDeviceStartMethod', 1))
            self.globals['trvc'][trvCtlrDevId]['setpointHeatDeviceStartDefault'] = float(trvcDev.pluginProps.get('setpointHeatDeviceStartDefault', 8))

            self.globals['trvc'][trvCtlrDevId]['nextScheduleExecutionTime'] = 'Not yet evaluated'

            self.globals['trvc'][trvCtlrDevId]['schedule1Enabled'] = bool(trvcDev.pluginProps.get('schedule1Enabled', False))
            if self.globals['trvc'][trvCtlrDevId]['schedule1Enabled']:
                self.globals['trvc'][trvCtlrDevId]['schedule1TimeOn'] = trvcDev.pluginProps.get('schedule1TimeOn', '00:00')
                self.globals['trvc'][trvCtlrDevId]['schedule1TimeOff'] = trvcDev.pluginProps.get('schedule1TimeOff', '00:00')
                self.globals['trvc'][trvCtlrDevId]['schedule1SetpointHeat'] = float(trvcDev.pluginProps.get('schedule1SetpointHeat', 0.0))
            else:
                self.globals['trvc'][trvCtlrDevId]['schedule1TimeOn'] = '00:00'
                self.globals['trvc'][trvCtlrDevId]['schedule1TimeOff'] = '00:00'
                self.globals['trvc'][trvCtlrDevId]['schedule1SetpointHeat'] = 0.0
            if not self.globals['trvc'][trvCtlrDevId]['schedule1Enabled'] or self.globals['trvc'][trvCtlrDevId]['schedule1SetpointHeat'] == 0.0:
                self.globals['trvc'][trvCtlrDevId]['schedule1SetpointHeatUi'] = 'Not Set'
                self.globals['trvc'][trvCtlrDevId]['schedule1TimeUi'] = 'Inactive'
            else:
                self.globals['trvc'][trvCtlrDevId]['schedule1SetpointHeatUi'] = '{} °C'.format(self.globals['trvc'][trvCtlrDevId]['schedule1SetpointHeat'])
                self.globals['trvc'][trvCtlrDevId]['schedule1TimeUi'] = '{} - {}'.format(self.globals['trvc'][trvCtlrDevId]['schedule1TimeOn'], self.globals['trvc'][trvCtlrDevId]['schedule1TimeOff'])

            self.globals['trvc'][trvCtlrDevId]['schedule2Enabled'] = bool(trvcDev.pluginProps.get('schedule2Enabled', False))
            if self.globals['trvc'][trvCtlrDevId]['schedule2Enabled']:
                self.globals['trvc'][trvCtlrDevId]['schedule2TimeOn'] = trvcDev.pluginProps.get('schedule2TimeOn', '00:00')
                self.globals['trvc'][trvCtlrDevId]['schedule2TimeOff'] = trvcDev.pluginProps.get('schedule2TimeOff', '00:00')
                self.globals['trvc'][trvCtlrDevId]['schedule2SetpointHeat'] = float(trvcDev.pluginProps.get('schedule2SetpointHeat', 0.0))
            else:
                self.globals['trvc'][trvCtlrDevId]['schedule2TimeOn'] = '00:00'
                self.globals['trvc'][trvCtlrDevId]['schedule2TimeOff'] = '00:00'
                self.globals['trvc'][trvCtlrDevId]['schedule2SetpointHeat'] = 0.0
            if not self.globals['trvc'][trvCtlrDevId]['schedule2Enabled'] or self.globals['trvc'][trvCtlrDevId]['schedule2SetpointHeat'] == 0.0:
                self.globals['trvc'][trvCtlrDevId]['schedule2SetpointHeatUi'] = 'Not Set'
                self.globals['trvc'][trvCtlrDevId]['schedule2TimeUi'] = 'Inactive'
            else:
                self.globals['trvc'][trvCtlrDevId]['schedule2SetpointHeatUi'] = '{} °C'.format(self.globals['trvc'][trvCtlrDevId]['schedule2SetpointHeat'])
                self.globals['trvc'][trvCtlrDevId]['schedule2TimeUi'] = '{} - {}'.format(self.globals['trvc'][trvCtlrDevId]['schedule2TimeOn'], self.globals['trvc'][trvCtlrDevId]['schedule2TimeOff'])

            self.globals['trvc'][trvCtlrDevId]['schedule3Enabled'] = bool(trvcDev.pluginProps.get('schedule3Enabled', False))
            if self.globals['trvc'][trvCtlrDevId]['schedule3Enabled']:
                self.globals['trvc'][trvCtlrDevId]['schedule3TimeOn'] = trvcDev.pluginProps.get('schedule3TimeOn', '00:00')
                self.globals['trvc'][trvCtlrDevId]['schedule3TimeOff'] = trvcDev.pluginProps.get('schedule3TimeOff', '00:00')
                self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeat'] = float(trvcDev.pluginProps.get('schedule3SetpointHeat', 0.0))
            else:
                self.globals['trvc'][trvCtlrDevId]['schedule3TimeOn'] = '00:00'
                self.globals['trvc'][trvCtlrDevId]['schedule3TimeOff'] = '00:00'
                self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeat'] = 0.0
            if not self.globals['trvc'][trvCtlrDevId]['schedule3Enabled'] or  self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeat'] == 0.0:
                self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeatUi'] = 'Not Set'
                self.globals['trvc'][trvCtlrDevId]['schedule3TimeUi'] = 'Inactive'
            else:
                self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeatUi'] = '{} °C'.format(self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeat'])
                self.globals['trvc'][trvCtlrDevId]['schedule3TimeUi'] = '{} - {}'.format(self.globals['trvc'][trvCtlrDevId]['schedule3TimeOn'], self.globals['trvc'][trvCtlrDevId]['schedule3TimeOff'])

            self.globals['trvc'][trvCtlrDevId]['schedule4Enabled'] = bool(trvcDev.pluginProps.get('schedule4Enabled', False))
            if self.globals['trvc'][trvCtlrDevId]['schedule4Enabled']:
                self.globals['trvc'][trvCtlrDevId]['schedule4TimeOn'] = trvcDev.pluginProps.get('schedule4TimeOn', '00:00')
                self.globals['trvc'][trvCtlrDevId]['schedule4TimeOff'] = trvcDev.pluginProps.get('schedule4TimeOff', '00:00')
                self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeat'] = float(trvcDev.pluginProps.get('schedule4SetpointHeat', 0.0))
            else:
                self.globals['trvc'][trvCtlrDevId]['schedule4TimeOn'] = '00:00'
                self.globals['trvc'][trvCtlrDevId]['schedule4TimeOff'] = '00:00'
                self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeat'] = 0.0
            if not self.globals['trvc'][trvCtlrDevId]['schedule4Enabled'] or  self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeat'] == 0.0:
                self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeatUi'] = 'Not Set'
                self.globals['trvc'][trvCtlrDevId]['schedule4TimeUi'] = 'Inactive'
            else:
                self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeatUi'] = '{} °C'.format(self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeat'])
                self.globals['trvc'][trvCtlrDevId]['schedule4TimeUi'] = '{} - {}'.format(self.globals['trvc'][trvCtlrDevId]['schedule4TimeOn'], self.globals['trvc'][trvCtlrDevId]['schedule4TimeOff'])

            # Following section of code is to save the values if the schedule is reset to as defined in the device configuration
            self.globals['trvc'][trvCtlrDevId]['scheduleReset1Enabled'] = self.globals['trvc'][trvCtlrDevId]['schedule1Enabled']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset1TimeOn']  = self.globals['trvc'][trvCtlrDevId]['schedule1TimeOn'] 
            self.globals['trvc'][trvCtlrDevId]['scheduleReset1TimeOff'] = self.globals['trvc'][trvCtlrDevId]['schedule1TimeOff']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset1TimeUi'] =  self.globals['trvc'][trvCtlrDevId]['schedule1TimeUi']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset1HeatSetpoint'] = self.globals['trvc'][trvCtlrDevId]['schedule1SetpointHeat'] 
            self.globals['trvc'][trvCtlrDevId]['scheduleReset2Enabled'] = self.globals['trvc'][trvCtlrDevId]['schedule2Enabled']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset2TimeOn']  = self.globals['trvc'][trvCtlrDevId]['schedule2TimeOn'] 
            self.globals['trvc'][trvCtlrDevId]['scheduleReset2TimeOff'] = self.globals['trvc'][trvCtlrDevId]['schedule2TimeOff'] 
            self.globals['trvc'][trvCtlrDevId]['scheduleReset2TimeUi'] =  self.globals['trvc'][trvCtlrDevId]['schedule2TimeUi']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset2HeatSetpoint'] = self.globals['trvc'][trvCtlrDevId]['schedule2SetpointHeat'] 
            self.globals['trvc'][trvCtlrDevId]['scheduleReset3Enabled'] = self.globals['trvc'][trvCtlrDevId]['schedule3Enabled']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset3TimeOn']  = self.globals['trvc'][trvCtlrDevId]['schedule3TimeOn'] 
            self.globals['trvc'][trvCtlrDevId]['scheduleReset3TimeOff'] = self.globals['trvc'][trvCtlrDevId]['schedule3TimeOff'] 
            self.globals['trvc'][trvCtlrDevId]['scheduleReset3TimeUi'] =  self.globals['trvc'][trvCtlrDevId]['schedule3TimeUi']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset3HeatSetpoint'] = self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeat'] 
            self.globals['trvc'][trvCtlrDevId]['scheduleReset4Enabled'] = self.globals['trvc'][trvCtlrDevId]['schedule4Enabled']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset4TimeOn']  = self.globals['trvc'][trvCtlrDevId]['schedule4TimeOn'] 
            self.globals['trvc'][trvCtlrDevId]['scheduleReset4TimeOff'] = self.globals['trvc'][trvCtlrDevId]['schedule4TimeOff'] 
            self.globals['trvc'][trvCtlrDevId]['scheduleReset4TimeUi'] =  self.globals['trvc'][trvCtlrDevId]['schedule4TimeUi']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset4HeatSetpoint'] = self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeat'] 

            self.globals['trvc'][trvCtlrDevId]['schedule1Fired'] = False  # NOT SURE IF THESES WILL BE USED ???
            self.globals['trvc'][trvCtlrDevId]['schedule2Fired'] = False
            self.globals['trvc'][trvCtlrDevId]['schedule3Fired'] = False
            self.globals['trvc'][trvCtlrDevId]['schedule4Fired'] = False

            self.globals['trvc'][trvCtlrDevId]['schedule1Active'] = False
            self.globals['trvc'][trvCtlrDevId]['schedule2Active'] = False
            self.globals['trvc'][trvCtlrDevId]['schedule3Active'] = False
            self.globals['trvc'][trvCtlrDevId]['schedule4Active'] = False

            self.globals['trvc'][trvCtlrDevId]['advanceActive'] = False
            self.globals['trvc'][trvCtlrDevId]['advanceStatusUi'] = ''
            self.globals['trvc'][trvCtlrDevId]['advanceActivatedTime'] = "Inactive"
            self.globals['trvc'][trvCtlrDevId]['advanceToScheduleTime'] = "Inactive"

            self.globals['trvc'][trvCtlrDevId]['boostMode'] = BOOST_MODE_INACTIVE
            self.globals['trvc'][trvCtlrDevId]['boostModeUi'] = BOOST_MODE_TRANSLATION[self.globals['trvc'][trvCtlrDevId]['boostMode']]
            self.globals['trvc'][trvCtlrDevId]['boostStatusUi'] = ''
            self.globals['trvc'][trvCtlrDevId]['boostActive'] = False
            self.globals['trvc'][trvCtlrDevId]['boostDeltaT'] = 0.0
            self.globals['trvc'][trvCtlrDevId]['boostSetpoint'] = 0.0
            self.globals['trvc'][trvCtlrDevId]['boostMinutes'] = 0
            self.globals['trvc'][trvCtlrDevId]['boostTimeEnd'] = "Inactive"
            self.globals['trvc'][trvCtlrDevId]['boostTimeStart'] = "Inactive"
            self.globals['trvc'][trvCtlrDevId]['boostSetpointToRestore'] = 0.0
            self.globals['trvc'][trvCtlrDevId]['boostSetpointInvokeRestore'] = False

            self.globals['trvc'][trvCtlrDevId]['deviceStartDatetime'] = str(self.currentTime)

            self.globals['trvc'][trvCtlrDevId]['extendActive'] = False
            self.globals['trvc'][trvCtlrDevId]['extendStatusUi'] = ''
            self.globals['trvc'][trvCtlrDevId]['extendIncrementMinutes'] = 0
            self.globals['trvc'][trvCtlrDevId]['extendMaximumMinutes'] = 0
            self.globals['trvc'][trvCtlrDevId]['extendMinutes'] = 0
            self.globals['trvc'][trvCtlrDevId]['extendActivatedTime'] = "Inactive"
            self.globals['trvc'][trvCtlrDevId]['extendScheduleOriginalTime'] = "Inactive"
            self.globals['trvc'][trvCtlrDevId]['extendScheduleNewTime'] = "Inactive"
            self.globals['trvc'][trvCtlrDevId]['extendLimitReached'] = False

            self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'] = float(indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['trvDevId'])].heatSetpoint)

            if self.globals['trvc'][trvCtlrDevId]['setpointHeatDeviceStartMethod'] == DEVICE_START_SETPOINT_DEVICE_MINIMUM:
                self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(trvcDev.pluginProps['setpointHeatMinimum'])
                self.generalLogger.info(u'\'{}\' Heat Setpoint set to device minimum value i.e. \'{}\''.format(trvcDev.name, self.globals['trvc'][trvCtlrDevId]['setpointHeat']))
            elif self.globals['trvc'][trvCtlrDevId]['setpointHeatDeviceStartMethod'] == DEVICE_START_SETPOINT_LEAVE_AS_IS:
                self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(indigo.devices[trvCtlrDevId].heatSetpoint)
                self.generalLogger.info(u'\'{}\' Heat Setpoint left unchanged i.e. \'{}\''.format(trvcDev.name, self.globals['trvc'][trvCtlrDevId]['setpointHeat']))
            elif  self.globals['trvc'][trvCtlrDevId]['setpointHeatDeviceStartMethod'] == DEVICE_START_SETPOINT_SPECIFIED:                
                self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatDeviceStartDefault'])
                self.generalLogger.info(u'\'{}\' Heat Setpoint set to specified \'Device Start\' value i.e. \'{}\''.format(trvcDev.name, self.globals['trvc'][trvCtlrDevId]['setpointHeat']))
            else:
                self.generalLogger.error(u'Error detected by TRV Plugin for device [{}] - Unknown method \'{}\' to set Device Start Heat Setpoint'.format(trvcDev.name, self.globals['trvc'][trvCtlrDevId]['setpointHeatDeviceStartMethod']))
                return

            self.globals['trvc'][trvCtlrDevId]['heatSetpointAdvance'] = 0
            self.globals['trvc'][trvCtlrDevId]['heatSetpointBoost'] = 0

            if self.globals['trvc'][trvCtlrDevId]['enableTrvOnOff']:
                self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'] = HVAC_OFF
                self.globals['trvc'][trvCtlrDevId]['hvacOperationMode'] = HVAC_OFF
            else:
                self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'] = HVAC_HEAT
                self.globals['trvc'][trvCtlrDevId]['hvacOperationMode'] = HVAC_HEAT

            self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_INITIALISATION

            self.globals['trvc'][trvCtlrDevId]['modeDatetimeChanged'] = self.currentTime

            if self.globals['trvc'][trvCtlrDevId]['trvSupportsTemperatureReporting']:
                self.globals['trvc'][trvCtlrDevId]['temperatureTrv'] = float(indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['trvDevId'])].temperatures[0])
            else:
                self.globals['trvc'][trvCtlrDevId]['temperatureTrv'] = 0.0

            self.globals['trvc'][trvCtlrDevId]['temperatureRemote'] = float(0.0)
            self.globals['trvc'][trvCtlrDevId]['temperatureRemotePreOffset'] = float(0.0)
            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:
                try:
                    self.globals['trvc'][trvCtlrDevId]['temperatureRemote'] = float(indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['remoteDevId'])].temperatures[0])  # e.g. Radiator Thermostat (HRT4-ZW)
                except AttributeError:
                    try:
                        self.globals['trvc'][trvCtlrDevId]['temperatureRemote'] = float(indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['remoteDevId'])].states['sensorValue'])  # e.g. Aeon 4 in 1 / Fibaro FGMS-001
                    except (AttributeError, KeyError):
                        try:
                            self.globals['trvc'][trvCtlrDevId]['temperatureRemote'] = float(indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['remoteDevId'])].states['temperature'])  # e.g. Oregon Scientific Temp Sensor
                        except (AttributeError, KeyError):
                            try:
                                self.globals['trvc'][trvCtlrDevId]['temperatureRemote'] = float(indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['remoteDevId'])].states['Temperature'])  # e.g. Netatmo
                            except (AttributeError, KeyError):
                                indigo.server.error(u'\'{}\' is an unknown Remote Thermostat type - Remote support disabled for TRV \'{}\''.format(indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].name, trvcDev.name))
                                self.globals['trvc'][trvCtlrDevId]['remoteDevId'] = 0  # Disable Remote Support 

            self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote'] = 0
            self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl'] = bool(trvcDev.pluginProps.get('remoteSetpointHeatControl', False))

            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] == 0:
                self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl'] = False
                self.globals['trvc'][trvCtlrDevId]['temperature'] = float(self.globals['trvc'][trvCtlrDevId]['temperatureTrv'])
            else:
                self.globals['trvc'][trvCtlrDevId]['remoteTempOffset'] = float(trvcDev.pluginProps.get('remoteTempOffset', 0.0))
                self.globals['trvc'][trvCtlrDevId]['temperatureRemotePreOffset'] = float(self.globals['trvc'][trvCtlrDevId]['temperatureRemote'])                
                self.globals['trvc'][trvCtlrDevId]['temperatureRemote'] = float(self.globals['trvc'][trvCtlrDevId]['temperatureRemote']) + float(self.globals['trvc'][trvCtlrDevId]['remoteTempOffset'])                 
                self.globals['trvc'][trvCtlrDevId]['temperature'] = float(self.globals['trvc'][trvCtlrDevId]['temperatureRemote'])
                self.globals['trvc'][trvCtlrDevId]['remoteDeltaMax'] = float(trvcDev.pluginProps.get('remoteDeltaMax', 5.0))


                if self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl']:
                    try:
                        setpoint = float(indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['remoteDevId'])].heatSetpoint)
                        if float(setpoint) < float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum']):
                            setpoint = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum'])
                        elif float(setpoint) > float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum']):
                            setpoint = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum'])
                        self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = setpoint
                        self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote'] = setpoint
                    except:
                        self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl'] = False     

            self.globals['trvc'][trvCtlrDevId]['zwaveEventWakeUpSentDisplayFix'] = ''  # Used to flip the Z-wave reporting around for Wakeup command (Indigo fix)
            self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountTrv'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountPreviousTrv'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwaveSentCountTrv'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwaveSentCountPreviousTrv'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeTrv'] = 'N/A'
            self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeTrv'] = 'N/A'
            self.globals['trvc'][trvCtlrDevId]['zwaveWakeupDelayTrv'] = False
            self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'] = int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].globalProps["com.perceptiveautomation.indigoplugin.zwave"]["zwWakeInterval"])

            if self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'] > 0:
                trvDevId = self.globals['trvc'][trvCtlrDevId]['trvDevId']
                nextWakeupMissedSeconds = (self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'] + 2) * 60  # Add 2 minutes to next expected wakeup
                if trvDevId in self.globals['timers']['zwaveWakeupCheck']:
                    self.globals['timers']['zwaveWakeupCheck'][trvDevId].cancel()
                self.globals['timers']['zwaveWakeupCheck'][trvDevId] = threading.Timer(float(nextWakeupMissedSeconds), self.zwaveWakeupMissedTriggered, [trvCtlrDevId, TRV, trvDevId])
                self.globals['timers']['zwaveWakeupCheck'][trvDevId].setDaemon(True)
                self.globals['timers']['zwaveWakeupCheck'][trvDevId].start()

            self.globals['trvc'][trvCtlrDevId]['zwaveLastSentCommandTrv'] = ''
            self.globals['trvc'][trvCtlrDevId]['zwaveLastReceivedCommandTrv'] = ''
            
            self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountRemote'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountPreviousRemote'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwaveSentCountRemote'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwaveSentCountPreviousRemote'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeRemote'] = 'N/A'
            self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeRemote'] = 'N/A'
            self.globals['trvc'][trvCtlrDevId]['zwaveWakeupDelayRemote'] = False
            self.globals['trvc'][trvCtlrDevId]['zwaveMonitoringEnabledRemote'] = False
            self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalRemote'] = int(0)
            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:
                remoteDevId = self.globals['trvc'][trvCtlrDevId]['remoteDevId']
                if indigo.devices[remoteDevId].protocol == indigo.kProtocol.ZWave:
                    try:
                        self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalRemote'] = int(indigo.devices[remoteDevId].globalProps["com.perceptiveautomation.indigoplugin.zwave"]["zwWakeInterval"])
                        self.globals['trvc'][trvCtlrDevId]['zwaveMonitoringEnabledRemote'] = True

                        if self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalRemote'] > 0:
                            nextWakeupMissedSeconds = (self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalRemote'] + 2) * 60  # Add 2 minutes to next expected wakeup
                            if remoteDevId in self.globals['timers']['zwaveWakeupCheck']:
                                self.globals['timers']['zwaveWakeupCheck'][remoteDevId].cancel()
                            self.globals['timers']['zwaveWakeupCheck'][remoteDevId] = threading.Timer(float(nextWakeupMissedSeconds), self.zwaveWakeupMissedTriggered, [trvCtlrDevId, REMOTE, remoteDevId])
                            self.globals['timers']['zwaveWakeupCheck'][remoteDevId].setDaemon(True)
                            self.globals['timers']['zwaveWakeupCheck'][remoteDevId].start()
                    except:
                        self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalRemote'] = int(0)
                else:
                    # self.generalLogger.debug("Protocol for device %s is '%s'" % (indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].name, indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].protocol))
                    self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalRemote'] = int(0)

            self.globals['trvc'][trvCtlrDevId]['zwaveLastSentCommandRemote'] = ''
            self.globals['trvc'][trvCtlrDevId]['zwaveLastReceivedCommandRemote'] = ''
            self.globals['trvc'][trvCtlrDevId]['zwavePendingHvac'] = False  # Used to differentiate between internally generated Z-Wave hvac command and UI generated Z-Wave hvac ommands

            self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointFlag'] = False  # Used to differentiate between internally generated Z-Wave setpoint command and UI generated Z-Wave setpoint ommands
            self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointSequence'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointValue'] = 0.0
            self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointFlag'] = False  # Used to differentiate between internally generated Z-Wave setpoint command and UI generated Z-Wave setpoint ommands
            self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointSequence'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointValue'] = 0.0

            self.globals['trvc'][trvCtlrDevId]['processLimeProtection'] = 'off'
            self.globals['trvc'][trvCtlrDevId]['limeProtectionCheckTime'] = 0
            self.globals['trvc'][trvCtlrDevId]['deltaIncreaseHeatSetpoint'] = 0.0
            self.globals['trvc'][trvCtlrDevId]['deltaIDecreaseHeatSetpoint'] = 0.0

            # Update device states

            self.keyValueList = [
                    {'key': 'hvacOperationMode', 'value': self.globals['trvc'][trvCtlrDevId]['hvacOperationMode']},
                    {'key': 'nextScheduleExecutionTime', 'value': self.globals['trvc'][trvCtlrDevId]['nextScheduleExecutionTime']},
                    {'key': 'schedule1Active', 'value': self.globals['trvc'][trvCtlrDevId]['schedule1Active']},
                    {'key': 'schedule1Enabled', 'value': self.globals['trvc'][trvCtlrDevId]['schedule1Enabled']},
                    {'key': 'schedule1TimeOn', 'value': self.globals['trvc'][trvCtlrDevId]['schedule1TimeOn']},
                    {'key': 'schedule1TimeOff', 'value': self.globals['trvc'][trvCtlrDevId]['schedule1TimeOff']},
                    {'key': 'schedule1TimeUi', 'value': self.globals['trvc'][trvCtlrDevId]['schedule1TimeUi']},
                    {'key': 'schedule1SetpointHeat', 'value': self.globals['trvc'][trvCtlrDevId]['schedule1SetpointHeatUi']},
                    {'key': 'schedule2Active', 'value': self.globals['trvc'][trvCtlrDevId]['schedule2Active']},
                    {'key': 'schedule2Enabled', 'value': self.globals['trvc'][trvCtlrDevId]['schedule2Enabled']},
                    {'key': 'schedule2TimeOn', 'value': self.globals['trvc'][trvCtlrDevId]['schedule2TimeOn']},
                    {'key': 'schedule2TimeOff', 'value': self.globals['trvc'][trvCtlrDevId]['schedule2TimeOff']},
                    {'key': 'schedule2TimeUi', 'value': self.globals['trvc'][trvCtlrDevId]['schedule2TimeUi']},
                    {'key': 'schedule2SetpointHeat', 'value': self.globals['trvc'][trvCtlrDevId]['schedule2SetpointHeatUi']},
                    {'key': 'schedule3Active', 'value': self.globals['trvc'][trvCtlrDevId]['schedule3Active']},
                    {'key': 'schedule3Enabled', 'value': self.globals['trvc'][trvCtlrDevId]['schedule3Enabled']},
                    {'key': 'schedule3TimeOn', 'value': self.globals['trvc'][trvCtlrDevId]['schedule3TimeOn']},
                    {'key': 'schedule3TimeOff', 'value': self.globals['trvc'][trvCtlrDevId]['schedule3TimeOff']},
                    {'key': 'schedule3TimeUi', 'value': self.globals['trvc'][trvCtlrDevId]['schedule3TimeUi']},
                    {'key': 'schedule3SetpointHeat', 'value': self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeatUi']},
                    {'key': 'schedule4Active', 'value': self.globals['trvc'][trvCtlrDevId]['schedule4Active']},
                    {'key': 'schedule4Enabled', 'value': self.globals['trvc'][trvCtlrDevId]['schedule4Enabled']},
                    {'key': 'schedule4TimeOn', 'value': self.globals['trvc'][trvCtlrDevId]['schedule4TimeOn']},
                    {'key': 'schedule4TimeOff', 'value': self.globals['trvc'][trvCtlrDevId]['schedule4TimeOff']},
                    {'key': 'schedule4TimeUi', 'value': self.globals['trvc'][trvCtlrDevId]['schedule4TimeUi']},
                    {'key': 'schedule4SetpointHeat', 'value': self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeatUi']},
                    {'key': 'setpointHeatOnDefault', 'value': self.globals['trvc'][trvCtlrDevId]['setpointHeatOnDefault']},
                    {'key': 'setpointHeatMinimum', 'value': self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum']},
                    {'key': 'setpointHeatMaximum', 'value': self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum']},
                    {'key': 'setpointHeatTrv', 'value': self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']},
                    {'key': 'setpointHeatRemote', 'value': self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote']},
                    {'key': 'temperature', 'value': self.globals['trvc'][trvCtlrDevId]['temperature']},
                    {'key': 'temperatureRemote', 'value': self.globals['trvc'][trvCtlrDevId]['temperatureRemote']},
                    {'key': 'temperatureRemotePreOffset', 'value': self.globals['trvc'][trvCtlrDevId]['temperatureRemotePreOffset']},
                    {'key': 'temperatureTrv', 'value': self.globals['trvc'][trvCtlrDevId]['temperatureTrv']},

                    {'key': 'advanceActive', 'value': self.globals['trvc'][trvCtlrDevId]['advanceActive']},
                    {'key': 'advanceStatusUi', 'value': self.globals['trvc'][trvCtlrDevId]['advanceStatusUi']},
                    {'key': 'advanceActivatedTime', 'value': self.globals['trvc'][trvCtlrDevId]['advanceActivatedTime']},
                    {'key': 'advanceToScheduleTime', 'value': self.globals['trvc'][trvCtlrDevId]['advanceToScheduleTime']},

                    {'key': 'boostActive', 'value': self.globals['trvc'][trvCtlrDevId]['boostActive']},
                    {'key': 'boostMode', 'value': self.globals['trvc'][trvCtlrDevId]['boostMode']},
                    {'key': 'boostModeUi', 'value': self.globals['trvc'][trvCtlrDevId]['boostModeUi']},
                    {'key': 'boostStatusUi', 'value': self.globals['trvc'][trvCtlrDevId]['boostStatusUi']},
                    {'key': 'boostDeltaT', 'value': self.globals['trvc'][trvCtlrDevId]['boostDeltaT']},
                    {'key': 'boostSetpoint', 'value': int(self.globals['trvc'][trvCtlrDevId]['boostSetpoint'])},
                    {'key': 'boostMinutes', 'value': self.globals['trvc'][trvCtlrDevId]['boostMinutes']},
                    {'key': 'boostTimeStart', 'value': self.globals['trvc'][trvCtlrDevId]['boostTimeStart']},
                    {'key': 'boostTimeEnd', 'value': self.globals['trvc'][trvCtlrDevId]['boostTimeEnd']},

                    {'key': 'extendActive', 'value': self.globals['trvc'][trvCtlrDevId]['extendActive']},
                    {'key': 'extendStatusUi', 'value': self.globals['trvc'][trvCtlrDevId]['extendStatusUi']},
                    {'key': 'extendMinutes', 'value': self.globals['trvc'][trvCtlrDevId]['extendMinutes']},
                    {'key': 'extendActivatedTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendActivatedTime']},
                    {'key': 'extendScheduleOriginalTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendScheduleOriginalTime']},
                    {'key': 'extendScheduleNewTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendScheduleNewTime']},
                    {'key': 'extendLimitReached', 'value': self.globals['trvc'][trvCtlrDevId]['extendLimitReached']},

                    {'key': 'eventReceivedDateTimeRemote', 'value': self.globals['trvc'][trvCtlrDevId]['lastSuccessfulCommRemote']},

                    {'key': 'zwaveEventReceivedDateTimeTrv', 'value': self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeTrv']},
                    {'key': 'zwaveEventReceivedDateTimeRemote', 'value': self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeRemote']},
                    {'key': 'zwaveEventSentDateTimeTrv', 'value': self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeTrv']},
                    {'key': 'zwaveEventSentDateTimeRemote', 'value': self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeRemote']},
                    {'key': 'valvePercentageOpen', 'value': self.globals['trvc'][trvCtlrDevId]['valvePercentageOpen']},
                    {'key': 'hvacHeaterIsOn', 'value': False},
                    {'key': 'setpointHeat', 'value': self.globals['trvc'][trvCtlrDevId]['setpointHeat']},
                    {'key': 'batteryLevel', 'value': int(self.globals['trvc'][trvCtlrDevId]['batteryLevel']), 'uiValue': '{}%'.format(self.globals['trvc'][trvCtlrDevId]['batteryLevel'])},
                    {'key': 'batteryLevelTrv', 'value': int(self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv']), 'uiValue': '{}%'.format(self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'])},
                    {'key': 'batteryLevelRemote', 'value': int(self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote']), 'uiValue': '{}%'.format(self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'])},
                    {'key': 'hvacOperationModeTrv', 'value': self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv']},
                    {'key': 'hvacOperationMode', 'value': self.globals['trvc'][trvCtlrDevId]['hvacOperationMode']},
                    {'key': 'controllerMode', 'value': self.globals['trvc'][trvCtlrDevId]['controllerMode']},
                    {'key': 'controllerModeUi', 'value': CONTROLLER_MODE_TRANSLATION[self.globals['trvc'][trvCtlrDevId]['controllerMode']]}
                ]

            self.keyValueList.append({'key': 'temperatureInput1', 'value': self.globals['trvc'][trvCtlrDevId]['temperature'], 'uiValue': '{:.1f} °C'.format(self.globals['trvc'][trvCtlrDevId]['temperature'])})
            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:
                if self.globals['trvc'][trvCtlrDevId]['trvSupportsTemperatureReporting']:
                    self.keyValueList.append({'key': 'temperatureInput2', 'value': self.globals['trvc'][trvCtlrDevId]['temperatureTrv'], 'uiValue': '{:.1f} °C'.format(self.globals['trvc'][trvCtlrDevId]['temperatureTrv'])})
                    self.keyValueList.append({'key': 'temperatureUi', 'value': 'R: {:.1f} °C, T: {:.1f} °C'.format(self.globals['trvc'][trvCtlrDevId]['temperatureRemote'], self.globals['trvc'][trvCtlrDevId]['temperatureTrv'])})
                else:
                    self.keyValueList.append({'key': 'temperatureUi', 'value': 'R: {:.1f} °C'.format(self.globals['trvc'][trvCtlrDevId]['temperatureRemote'])})

            else:
                self.keyValueList.append({'key': 'temperatureUi', 'value': 'T: {:.1f} °C'.format(self.globals['trvc'][trvCtlrDevId]['temperatureTrv'])})

            trvcDev.updateStatesOnServer(self.keyValueList)

            trvcDev.updateStateImageOnServer(indigo.kStateImageSel.HvacAutoMode)  # HvacOff - HvacHeatMode - HvacHeating - HvacAutoMode

            # Check if CSV Files need initialising

            if self.globals['trvc'][trvCtlrDevId]['updateCsvFile']:
                if self.globals['trvc'][trvCtlrDevId]['updateAllCsvFiles']:
                    self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES, trvCtlrDevId, None])
                else:
                    self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['setpointHeat', float(self.globals['trvc'][trvCtlrDevId]['setpointHeat'])]])
                    self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['temperatureTrv', float(self.globals['trvc'][trvCtlrDevId]['temperatureTrv'])]])
                    self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['setpointHeatTrv', float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])]])
                    if self.globals['trvc'][trvCtlrDevId]['valveDevId'] != 0:
                        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['valvePercentageOpen', int(self.globals['trvc'][trvCtlrDevId]['valvePercentageOpen'])]])
                    if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:
                        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['temperatureRemote', float(self.globals['trvc'][trvCtlrDevId]['temperatureRemote'])]])
                        if self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl']:
                            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['setpointHeatRemote', float(self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote'])]])

            # Set-up schedules
            scheduleSetpointOff = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum'])
            self.globals['schedules'][trvCtlrDevId]['default'][0] = ('00:00', scheduleSetpointOff, 0, False)  # Start of Day
            self.globals['schedules'][trvCtlrDevId]['default'][240000] = ('24:00', scheduleSetpointOff, 9, False)  # End of Day

            if self.globals['trvc'][trvCtlrDevId]['schedule1Enabled']:
                scheduleTimeOnUi = self.globals['trvc'][trvCtlrDevId]['schedule1TimeOn']
                scheduleTimeOn = int(scheduleTimeOnUi.replace(':','')) * 100  # Add in Seconds
                scheduleTimeOffUi =self.globals['trvc'][trvCtlrDevId]['schedule1TimeOff']
                scheduleTimeOff = int(scheduleTimeOffUi.replace(':','')) * 100  # Add in Seconds
                scheduleSetpointOn = float(self.globals['trvc'][trvCtlrDevId]['schedule1SetpointHeat'])
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 1, True)
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 1, False)

            if self.globals['trvc'][trvCtlrDevId]['schedule2Enabled']:
                scheduleTimeOnUi = self.globals['trvc'][trvCtlrDevId]['schedule2TimeOn']
                scheduleTimeOn = int(scheduleTimeOnUi.replace(':','')) * 100  # Add in Seconds
                scheduleTimeOffUi =self.globals['trvc'][trvCtlrDevId]['schedule2TimeOff']
                scheduleTimeOff = int(scheduleTimeOffUi.replace(':','')) * 100  # Add in Seconds
                scheduleSetpointOn = float(self.globals['trvc'][trvCtlrDevId]['schedule2SetpointHeat'])
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 2, True)
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 2, False)

            if self.globals['trvc'][trvCtlrDevId]['schedule3Enabled']:
                scheduleTimeOnUi = self.globals['trvc'][trvCtlrDevId]['schedule3TimeOn']
                scheduleTimeOn = int(scheduleTimeOnUi.replace(':','')) * 100  # Add in Seconds
                scheduleTimeOffUi =self.globals['trvc'][trvCtlrDevId]['schedule3TimeOff']
                scheduleTimeOff = int(scheduleTimeOffUi.replace(':','')) * 100  # Add in Seconds
                scheduleSetpointOn = float(self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeat'])
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 3, True)
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 3, False)

            if self.globals['trvc'][trvCtlrDevId]['schedule4Enabled']:
                scheduleTimeOnUi = self.globals['trvc'][trvCtlrDevId]['schedule4TimeOn']
                scheduleTimeOn = int(scheduleTimeOnUi.replace(':','')) * 100  # Add in Seconds
                scheduleTimeOffUi =self.globals['trvc'][trvCtlrDevId]['schedule4TimeOff']
                scheduleTimeOff = int(scheduleTimeOffUi.replace(':','')) * 100  # Add in Seconds
                scheduleSetpointOn = float(self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeat'])
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 4, True)
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 4, False)

            self.globals['schedules'][trvCtlrDevId]['default'] = collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['default'].items()))
            self.globals['schedules'][trvCtlrDevId]['running'] = self.globals['schedules'][trvCtlrDevId]['default'].copy()
            self.globals['schedules'][trvCtlrDevId]['dynamic'] = self.globals['schedules'][trvCtlrDevId]['default'].copy()

            if int(self.globals['trvc'][trvCtlrDevId]['trvDevId']) not in self.globals['devicesToTrvControllerTable'].keys():
                self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['trvDevId']] = dict()
            self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['trvDevId']]['type'] = TRV
            self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['trvDevId']]['trvControllerId'] = int(trvCtlrDevId)

            if self.globals['trvc'][trvCtlrDevId]['valveDevId'] != 0:
                if int(self.globals['trvc'][trvCtlrDevId]['valveDevId']) not in self.globals['devicesToTrvControllerTable'].keys():
                    self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['valveDevId']] = dict()
                self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['valveDevId']]['type'] = VALVE
                self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['valveDevId']]['trvControllerId'] = int(trvCtlrDevId)

            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:
                if int(self.globals['trvc'][trvCtlrDevId]['remoteDevId']) not in self.globals['devicesToTrvControllerTable'].keys():
                    self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['remoteDevId']] = dict()
                self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['remoteDevId']]['type'] = REMOTE
                self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['remoteDevId']]['trvControllerId'] = int(trvCtlrDevId)

            # if self.globals['trvc'][trvCtlrDevId]['valveDevId'] != 0:
            #     if int(self.globals['trvc'][trvCtlrDevId]['remoteDevId']) not in self.globals['devicesToTrvControllerTable'].keys():
            #         self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['remoteDevId']] = dict()
            #     self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['remoteDevId']]['type'] = REMOTE
            #     self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['remoteDevId']]['trvControllerId'] = int(trvCtlrDevId)

            try:
                heatingId =int(self.globals['trvc'][trvCtlrDevId]['heatingId'])
                if heatingId == 0:
                    heatingDeviceUi = 'No Device Heat Source control required.'
                else:
                    heatingDeviceUi = 'Device Heat Source \'{}\''.format(indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['heatingId'])].name)

                heatingVarId =int(self.globals['trvc'][trvCtlrDevId]['heatingVarId'])
                if heatingVarId == 0:
                    heatingVarUi = 'No Variable Heat Source control required.'
                else:
                    heatingVarUi = 'Variable Heat Source \'{}\''.format(indigo.variables[int(self.globals['trvc'][trvCtlrDevId]['heatingVarId'])].name)

                if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] == 0:
                    if not self.globals['trvc'][trvCtlrDevId]['trvSupportsTemperatureReporting']:
                        self.generalLogger.error(u'TRV Controller can\'t control TRV \'{}\' as the TRV doesn\'t report temperature and there is no Remote Stat defined!'.format(trvcDev.name, indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']]))
                        self.globals['trvc'][trvCtlrDevId]['deviceStarted'] = True
                        return
                    else:
                        self.generalLogger.info(u'Started \'{}\': Controlling TRV \'{}\'; {}'.format(trvcDev.name, indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['trvDevId'])].name, heatingDeviceUi))
                else:
                    self.generalLogger.info(u'Started \'{}\': Controlling TRV \'{}\'; Remote thermostat \'{}\'; {}; {}'.format(trvcDev.name, indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['trvDevId'])].name, indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['remoteDevId'])].name, heatingDeviceUi, heatingVarUi))

                self.globals['trvc'][trvCtlrDevId]['deviceStarted'] = True
                self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_DELAY_COMMAND, trvCtlrDevId, [CMD_PROCESS_HEATING_SCHEDULE, 2.0, None]])            

            except StandardError, err:
                self.generalLogger.error(u'StandardError [TRV Props ERROR AGAIN] detected in TRV Plugin [deviceStartComm of device \'{}\']. Line \'{}\' has error=\'{}\''.format(trvcDev.name, sys.exc_traceback.tb_lineno, err))   
            except:
                self.generalLogger.error("TRV Props ERROR AGAIN!!!!")

        except StandardError, err:
            self.generalLogger.error(u'StandardError detected in TRV Plugin [deviceStartComm of device \'{}\']. Line \'{}\' has error=\'{}\''.format(trvcDev.name, sys.exc_traceback.tb_lineno, err))   

    def deviceStopComm(self, trvcDev):

        self.methodTracer.threaddebug(u'Main Plugin Method')

        trvCtlrDevId = trvcDev.id

        if not self.globals['trvc'][trvCtlrDevId]['deviceStarted']:
            self.generalLogger.debug(u'controlTrv: \'{}\' device stopping but startup not yet completed'.format(trvcDev.name)) 

        self.globals['trvc'][trvCtlrDevId]['deviceStarted'] = False

        if 'trvDevId' in self.globals['trvc'][trvCtlrDevId] and self.globals['trvc'][trvCtlrDevId]['trvDevId'] != 0:
            self.globals['zwave']['WatchList'].discard(int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address))
        if 'remoteDevId' in self.globals['trvc'][trvCtlrDevId] and self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:

            if indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].protocol == indigo.kProtocol.ZWave:
                self.globals['zwave']['WatchList'].discard(int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].address))
        self.generalLogger.info("Stopping '%s'" % (trvcDev.name))
