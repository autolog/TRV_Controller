#! /usr/bin/env python
# -*- coding: utf-8 -*-
#
# TRV Controller © Autolog 2020 - 2022

# noinspection PyUnresolvedReferences, PyPep8Naming

try:
    # noinspection PyUnresolvedReferences
    import indigo
except ImportError:
    pass

import collections
import datetime
import logging
import platform
import queue
import operator
import sys
import threading
import traceback
import xml.etree.ElementTree as eTree

from constants import *
from trvHandler import ThreadTrvHandler
from delayHandler import ThreadDelayHandler
from zwave_interpreter.zwave_interpreter import *
from zwave_interpreter.zwave_command_class_wake_up import *
from zwave_interpreter.zwave_command_class_switch_multilevel import *

ZW_THERMOSTAT_SETPOINT_SET = 0x01
ZW_THERMOSTAT_MODE_SET = 0x01


# noinspection PyPep8Naming
def convertListToHexStr(byteList):
    return ' '.join(["%02X" % byte for byte in byteList])


# noinspection PyPep8Naming
def secondsFromHHMM(hhmm):
    # Convert str('HH:MM' to INT(seconds))
    hh = int(hhmm[0:2])
    mm = int(hhmm[3:5])
    seconds = (hh * 3600) + (mm * 60) 
    return seconds


# noinspection PyPep8Naming
def calculateSecondsUntilSchedulesRestated():
    # Calculate number of seconds until five minutes after next midnight
    tomorrow = datetime.datetime.now() + datetime.timedelta(1)
    midnight = datetime.datetime(year=tomorrow.year, month=tomorrow.month, day=tomorrow.day, hour=0, minute=0, second=0)
    secondsToMidnight = int((midnight - datetime.datetime.now()).seconds)  # Seconds to midnight

    # secondsSinceMidnight = (24 * 60 * 60) - secondsToMidnight

    secondsInFiveMinutes = (5 * 60)  # 5 minutes in seconds

    secondsUntilSchedulesRestated = secondsToMidnight + secondsInFiveMinutes  # Calculate number of seconds until 5 minutes after next midnight

    # secondsUntilSchedulesRestated = 60  # TESTING = 1 Minute
    return secondsUntilSchedulesRestated


# noinspection PyPep8Naming
class Plugin(indigo.PluginBase):

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):

        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        # Initialise dictionary to store plugin Globals
        self.globals = dict()

        self.globals['zwave'] = dict()
        self.globals['zwave']['addressToDevice'] = dict()
        self.globals['zwave']['WatchList'] = set()  # TRVs, Valves and Remotes associated with a TRV Controllers will get added to this SET on TRV Controller device start
        self.globals['zwave']['node_to_device_name'] = dict()

        # # Initialise Indigo plugin info
        # self.globals[PLUGIN_INFO] = {}
        # self.globals[PLUGIN_INFO][PLUGIN_ID] = plugin_id
        # self.globals[PLUGIN_INFO][PLUGIN_DISPLAY_NAME] = plugin_display_name
        # self.globals[PLUGIN_INFO][PLUGIN_VERSION] = plugin_version
        # self.globals[PLUGIN_INFO][PATH] = indigo.server.getInstallFolderPath()
        # self.globals[PLUGIN_INFO][API_VERSION] = indigo.server.apiVersion
        # self.globals[PLUGIN_INFO][ADDRESS] = indigo.server.address


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
        self.globals['debug']['delayHandler'] = logging.INFO  # For debugging Delay handler thread
        self.globals['debug']['polling'] = logging.INFO  # For polling debugging

        self.globals['debug']['previousGeneral'] = logging.INFO  # For general debugging of the main thread
        self.globals['debug']['previousTrvHandler'] = logging.INFO  # For debugging TRV handler thread 
        self.globals['debug']['previousDelayHandler'] = logging.INFO  # For debugging Delay handler thread
        self.globals['debug']['previousPolling'] = logging.INFO  # For polling debugging

        # Setup Logging - Logging info:
        #   self.indigo_log_handler - writes log messages to Indigo Event Log
        #   self.plugin_file_handler - writes log messages to the plugin log

        log_format = logging.Formatter("%(asctime)s.%(msecs)03d\t%(levelname)-12s\t%(name)s.%(funcName)-25s %(msg)s", datefmt="%Y-%m-%d %H:%M:%S")
        self.plugin_file_handler.setFormatter(log_format)
        self.plugin_file_handler.setLevel(K_LOG_LEVEL_INFO)  # Logging Level for plugin log file
        self.indigo_log_handler.setLevel(K_LOG_LEVEL_INFO)   # Logging level for Indigo Event Log

        self.logger = logging.getLogger("Plugin.TRV")

        # Now logging is set-up, output Initialising Message
        startup_message_ui = "\n"  # Start with a line break
        startup_message_ui += f"{' Initialising TRV Controller Plugin Plugin ':={'^'}130}\n"
        startup_message_ui += f"{'Plugin Name:':<31} {self.globals['pluginInfo']['pluginDisplayName']}\n"
        startup_message_ui += f"{'Plugin Version:':<31} {self.globals['pluginInfo']['pluginVersion']}\n"
        startup_message_ui += f"{'Plugin ID:':<31} {self.globals['pluginInfo']['pluginId']}\n"
        startup_message_ui += f"{'Indigo Version:':<31} {indigo.server.version}\n"
        startup_message_ui += f"{'Indigo License:':<31} {indigo.server.licenseStatus}\n"
        startup_message_ui += f"{'Indigo API Version:':<31} {indigo.server.apiVersion}\n"
        machine = platform.machine()
        startup_message_ui += f"{'Architecture:':<31} {machine}\n"
        sys_version = sys.version.replace("\n", "")
        startup_message_ui += f"{'Python Version:':<31} {sys_version}\n"
        startup_message_ui += f"{'Mac OS Version:':<31} {platform.mac_ver()[0]}\n"
        startup_message_ui += f"{'':={'^'}130}\n"
        self.logger.info(startup_message_ui)

        # Initialise dictionary to store configuration info
        self.globals['config'] = dict()

        # Initialise dictionary to store internal details about TRV Controller devices
        self.globals['trvc'] = dict()

        # Initialise dictionary to store internal details about heating (Boiler) devices and variables
        self.globals['heaterDevices'] = dict()
        self.globals['heaterVariables'] = dict()

        # Initialise dictionary to store message queues
        self.globals['queues'] = dict()
        self.globals['queues']['trvHandler'] = dict()
        self.globals['queues']['delay'] = dict()
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
        self.globals['threads']['delayHandler'] = dict()  # There is only one 'delayHandler' thread for all TRV devices

        self.globals['threads']['runConcurrentActive'] = False

        self.globals['lock'] = threading.Lock()
        
        self.globals['devicesToTrvControllerTable'] = dict()

        # Initialise dictionary for constants
        self.globals['constant'] = dict()
        self.globals['constant']['defaultDatetime'] = datetime.datetime.strptime('2000-01-01', '%Y-%m-%d')

        # Setup dictionary of supported TRV models
        xmlFile = f'{self.globals["pluginInfo"]["path"]}/Plugins/TRV.indigoPlugin/Contents/Resources/supportedThermostatModels.xml'
        tree = eTree.parse(xmlFile)
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

            # self.logger.error(f'XML [SUPPORTED TRV MODEL] =\n{self.globals["supportedTrvModels"][trv_model_name]}')

        # Setup dictionary of fully supported Heat Source Controller Devices
        xmlFile = f'{self.globals["pluginInfo"]["path"]}/Plugins/TRV.indigoPlugin/Contents/Resources/supportedHeatSourceControllers.xml'
        tree = eTree.parse(xmlFile)
        root = tree.getroot()
        self.globals['supportedHeatSourceControllers'] = dict()
        for model in root.findall('model'):
            heat_source_controller_model_name = model.get('name')
            self.globals['supportedHeatSourceControllers'][heat_source_controller_model_name] = ''

            # self.logger.error(f'XML [SUPPORTED HEAT SOURCE CONTROLLER] =\n{heat_source_controller_model_name}')

        # Set Plugin Config Values
        self.closedPrefsConfigUi(pluginPrefs, False)

        # TODO: Remove below as actioned in startup method - 18-March-2022
        #self.zwi = ZwaveInterpreter(self.logger, indigo.devices)  # Instantiate and initialise Z-Wave Interpreter Class
        # Instantiate and initialise Z-Wave Interpreter Class
        # self.zwi = ZwaveInterpreter(self.exception_handler, self.logger, indigo.devices)  # noqa [Defined outside __init__] Instantiate and initialise Z-Wave Interpreter Object
 
    def __del__(self):

        indigo.PluginBase.__del__(self)

    def exception_handler(self, exception_error_message, log_failing_statement):
        filename, line_number, method, statement = traceback.extract_tb(sys.exc_info()[2])[-1]
        module = filename.split('/')
        log_message = f"'{exception_error_message}' in module '{module[-1]}', method '{method}'"
        if log_failing_statement:
            log_message = log_message + f"\n   Failing statement [line {line_number}]: '{statement}'"
        else:
            log_message = log_message + f" at line {line_number}"
        self.logger.error(log_message)

    def actionControlThermostat(self, action, dev):

        self.logger.debug(f' Thermostat \'{dev.name}\', Action received: \'{action.description}\'')
        self.logger.debug(f'... Action details:\n{action}\n')

        trvCtlrDevId = dev.id

        # ##### SET HVAC MODE ######
        if action.thermostatAction == indigo.kThermostatAction.SetHvacMode:
            hvacMode = action.actionMode
            if hvacMode == HVAC_COOL or hvacMode == HVAC_AUTO:  # Don't allow HVAC Mode of Cool or Auto
                self.logger.error(f'TRV Controller  \'{dev.name}\' does not support action \'{action.description}\' - request ignored')
            else:

                # dev.updateStateOnServer('hvacOperationMode', action.actionMode)

                queuedCommand = CMD_UPDATE_TRV_CONTROLLER_STATES
                updateList = dict()
                updateList[UPDATE_CONTROLLER_HVAC_OPERATION_MODE] = hvacMode
                updateList[UPDATE_CONTROLLER_MODE] = CONTROLLER_MODE_UI
                self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, self.globals['deviceUpdatedSequenceCount'], queuedCommand, trvCtlrDevId, [updateList, ]])

        # ###### DECREASE HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.DecreaseHeatSetpoint:
            newSetpoint = dev.heatSetpoint - action.actionValue

            if newSetpoint < float(self.globals['trvc'][dev.id]['setpointHeatMinimum']):
                if dev.heatSetpoint > float(self.globals['trvc'][dev.id]['setpointHeatMinimum']):
                    newSetpoint = float(self.globals['trvc'][dev.id]['setpointHeatMinimum'])
                else:
                    self.logger.info(f'TRV Controller  \'{dev.name}\' Minimum Heat Setpoint is \'{self.globals["trvc"][dev.id]["setpointHeatMinimum"]}\' - Decrease Heat Setpoint request ignored')
                    return

            # keyValueList = [
            #         {'key': 'controllerMode', 'value': CONTROLLER_MODE_UI},
            #         {'key': 'controllerModeUi', 'value':  CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_UI]},
            #         {'key': 'setpointHeat', 'value': newSetpoint}
            #     ]
            # dev.updateStatesOnServer(keyValueList)

            queuedCommand = CMD_UPDATE_TRV_CONTROLLER_STATES
            updateList = dict()
            updateList[UPDATE_CONTROLLER_HEAT_SETPOINT] = newSetpoint
            updateList[UPDATE_CONTROLLER_MODE] = CONTROLLER_MODE_UI
            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, self.globals['deviceUpdatedSequenceCount'], queuedCommand, trvCtlrDevId, [updateList, ]])

            # ###### INCREASE HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.IncreaseHeatSetpoint:
            newSetpoint = dev.heatSetpoint + action.actionValue

            if newSetpoint > float(self.globals['trvc'][dev.id]['setpointHeatMaximum']):
                if dev.heatSetpoint < float(self.globals['trvc'][dev.id]['setpointHeatMaximum']):
                    newSetpoint = float(self.globals['trvc'][dev.id]['setpointHeatMaximum'])
                else:
                    self.logger.info(f'TRV Controller  \'{dev.name}\' Maximum Heat Setpoint is \'{self.globals["trvc"][dev.id]["setpointHeatMaximum"]}\' - Increase Heat Setpoint request ignored')
                    return

            # keyValueList = [
            #         {'key': 'controllerMode', 'value': CONTROLLER_MODE_UI},
            #         {'key': 'controllerModeUi', 'value':  CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_UI]},
            #         {'key': 'setpointHeat', 'value': newSetpoint}
            #     ]
            # dev.updateStatesOnServer(keyValueList)

            queuedCommand = CMD_UPDATE_TRV_CONTROLLER_STATES
            updateList = dict()
            updateList[UPDATE_CONTROLLER_HEAT_SETPOINT] = newSetpoint
            updateList[UPDATE_CONTROLLER_MODE] = CONTROLLER_MODE_UI
            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, self.globals['deviceUpdatedSequenceCount'], queuedCommand, trvCtlrDevId, [updateList, ]])

        # ###### SET HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.SetHeatSetpoint:
            newSetpoint = action.actionValue

            # keyValueList = [
            #         {'key': 'controllerMode', 'value': CONTROLLER_MODE_UI},
            #         {'key': 'controllerModeUi', 'value':  CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_UI]},
            #         {'key': 'setpointHeat', 'value': newSetpoint}
            #     ]
            # dev.updateStatesOnServer(keyValueList)

            queuedCommand = CMD_UPDATE_TRV_CONTROLLER_STATES
            updateList = dict()
            updateList[UPDATE_CONTROLLER_HEAT_SETPOINT] = newSetpoint
            updateList[UPDATE_CONTROLLER_MODE] = CONTROLLER_MODE_UI
            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, self.globals['deviceUpdatedSequenceCount'], queuedCommand, trvCtlrDevId, [updateList, ]])

        # ###### REQUEST STATUS ALL ETC ######
        elif action.thermostatAction in [indigo.kThermostatAction.RequestStatusAll, indigo.kThermostatAction.RequestMode,
                                         indigo.kThermostatAction.RequestEquipmentState, indigo.kThermostatAction.RequestTemperatures, indigo.kThermostatAction.RequestHumidities,
                                         indigo.kThermostatAction.RequestDeadbands, indigo.kThermostatAction.RequestSetpoints]:
            if self.globals['trvc'][action.deviceId]['trvDevId'] != 0:
                indigo.device.statusRequest(self.globals['trvc'][action.deviceId]['trvDevId'])
            if self.globals['trvc'][action.deviceId]['remoteDevId'] != 0:
                indigo.device.statusRequest(self.globals['trvc'][action.deviceId]['remoteDevId'])
        else:
            self.logger.error(f'Unknown Action for TRV Controller \'{dev.name}\': Action \'{action.description}\' Ignored')

    # noinspection PyUnusedLocal
    def closedDeviceConfigUi(self, valuesDict, userCancelled, typeId, trvCtlrDevId):
        # valuesDict, typeId, trvCtlrDevId arguments are not used
        try:
            self.logger.debug(f'\'closedDeviceConfigUi\' called with userCancelled = {str(userCancelled)}')

            if userCancelled:
                return

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        try:

            self.logger.threaddebug(f'\'closePrefsConfigUi\' called with userCancelled = {str(userCancelled)}')

            if userCancelled:
                return

            self.globals['config']['disableHeatSourceDeviceListFilter'] = valuesDict.get('disableHeatSourceDeviceListFilter', False)

            # Delay Queue Options
            self.globals['config']['delayQueueSeconds'] = int(valuesDict.get("delayQueueSeconds", 0))

            # CSV File Handling (for e.g. Matplotlib plugin)
            self.globals['config']['csvStandardEnabled'] = valuesDict.get("csvStandardEnabled", False)
            self.globals['config']['csvPostgresqlEnabled'] = valuesDict.get("csvPostgresqlEnabled", False)
            self.globals['config']['postgresqlUser'] = valuesDict.get("postgresqlUser", '')
            self.globals['config']['postgresqlPassword'] = valuesDict.get("postgresqlPassword", '')
            self.globals['config']['csvPath'] = valuesDict.get("csvPath", '')
            self.globals['config']['csvPrefix'] = valuesDict.get("csvPrefix", 'TRV_Plugin')

            # Create TRV Variable folder name (if required)
            self.globals['config']['trvVariableFolderName'] = valuesDict.get("trvVariableFolderName", 'TRV')
            if self.globals['config']['trvVariableFolderName'] == '':
                self.globals['config']['trvVariableFolderId'] = 0  # Not required
            else:
                if self.globals['config']['trvVariableFolderName'] not in indigo.variables.folders:
                    indigo.variables.folder.create(self.globals['config']['trvVariableFolderName'])
                self.globals['config']['trvVariableFolderId'] = indigo.variables.folders.getId(self.globals['config']['trvVariableFolderName'])

            # Check monitoring / debug / filtered IP address options
            # Get required Event Log and Plugin Log logging levels
            plugin_log_level = int(valuesDict.get("pluginLogLevel", K_LOG_LEVEL_INFO))
            event_log_level = int(valuesDict.get("eventLogLevel", K_LOG_LEVEL_INFO))

            # Ensure following logging level messages are output
            self.indigo_log_handler.setLevel(K_LOG_LEVEL_INFO)
            self.plugin_file_handler.setLevel(K_LOG_LEVEL_INFO)

            # Output required logging levels and TP Message Monitoring requirement to logs
            self.logger.info(f"Logging to Indigo Event Log at the '{K_LOG_LEVEL_TRANSLATION[event_log_level]}' level")
            self.logger.info(f"Logging to Plugin Event Log at the '{K_LOG_LEVEL_TRANSLATION[plugin_log_level]}' level")

            # Now set required logging levels
            self.indigo_log_handler.setLevel(event_log_level)
            self.plugin_file_handler.setLevel(plugin_log_level)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def deviceStartComm(self, trvcDev):

        try:
            trvCtlrDevId = trvcDev.id

            # Following code makes sure that the polling Sequence is maintained across Device start
            # pollingSequence = 0
            # if trvCtlrDevId in self.globals['trvc']:
            #     if 'pollingSequence' in self.globals['trvc'][trvCtlrDevId]:
            #         pollingSequence = self.globals['trvc'][trvCtlrDevId]['pollingSequence']

            self.globals['trvc'][trvCtlrDevId] = dict()

            # self.globals['trvc'][trvCtlrDevId]['pollingSequence'] = pollingSequence

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

            if (trvcDev.pluginProps.get('version', '0.0')) != self.globals['pluginInfo']['pluginVersion']:
                pluginProps = trvcDev.pluginProps
                pluginProps["version"] = self.globals['pluginInfo']['pluginVersion']
                trvcDev.replacePluginPropsOnServer(pluginProps)
                return

            currentTime = indigo.server.getTime()

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
            self.globals['trvc'][trvCtlrDevId]['pollingSchedulesNotEnabled'] = float(int(trvcDev.pluginProps.get('pollingSchedulesNotEnabled', 30)) * 60.0)
            self.globals['trvc'][trvCtlrDevId]['pollingBoostEnabled'] = float(int(trvcDev.pluginProps.get('pollingBoostEnabled', 5)) * 60.0)
            self.globals['trvc'][trvCtlrDevId]['pollingSeconds'] = 0.0

            self.globals['trvc'][trvCtlrDevId]['advancedOption'] = ADVANCED_OPTION_NONE
            self.globals['trvc'][trvCtlrDevId]['enableTrvOnOff'] = False
            if self.globals['trvc'][trvCtlrDevId]['trvDevId'] != 0:
                if trvcDev.address != indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address:
                    pluginProps = trvcDev.pluginProps
                    pluginProps["address"] = indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address
                    trvcDev.replacePluginPropsOnServer(pluginProps)
                    return

                self.globals['trvc'][trvCtlrDevId]['supportsHvacOnOff'] = bool(trvcDev.pluginProps.get('supportsHvacOnOff', False))
                if self.globals['trvc'][trvCtlrDevId]['supportsHvacOnOff']:
                    self.globals['trvc'][trvCtlrDevId]['enableTrvOnOff'] = bool(trvcDev.pluginProps.get('enableTrvOnOff', False))
                self.globals['trvc'][trvCtlrDevId]['trvSupportsManualSetpoint'] = bool(trvcDev.pluginProps.get('supportsManualSetpoint', False))
                self.globals['trvc'][trvCtlrDevId]['trvSupportsTemperatureReporting'] = bool(trvcDev.pluginProps.get('supportsTemperatureReporting', False))
                self.logger.debug(
                    f'TRV SUPPORTS TEMPERATURE REPORTING: \'{indigo.devices[self.globals["trvc"][trvCtlrDevId]["trvDevId"]].name}\' = {self.globals["trvc"][trvCtlrDevId]["trvSupportsTemperatureReporting"]} ')

                self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address)] = dict()
                self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address)]['devId'] = self.globals['trvc'][trvCtlrDevId]['trvDevId']
                self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address)]['type'] = TRV
                self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address)]['trvcId'] = trvCtlrDevId
                self.globals['zwave']['WatchList'].add(int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address))

                for dev in indigo.devices:
                    if dev.address == trvcDev.address and dev.id != self.globals['trvc'][trvCtlrDevId]['trvDevId']:
                        if dev.model == 'Thermostat (Spirit)':
                            advancedOption = int(trvcDev.pluginProps.get('advancedOption', ADVANCED_OPTION_NOT_SET))
                            if advancedOption == ADVANCED_OPTION_NOT_SET:
                                valveAssistance = bool(trvcDev.pluginProps.get('valveAssistance', True))
                                if valveAssistance:
                                    advancedOption = ADVANCED_OPTION_VALVE_ASSISTANCE
                                else:
                                    advancedOption = ADVANCED_OPTION_NONE
                            self.globals['trvc'][trvCtlrDevId]['advancedOption'] = advancedOption

                            if advancedOption == ADVANCED_OPTION_FIRMWARE_WORKAROUND or advancedOption == ADVANCED_OPTION_VALVE_ASSISTANCE:

                                self.globals['trvc'][trvCtlrDevId]['valveDevId'] = dev.id
                                self.globals['trvc'][trvCtlrDevId]['valvePercentageOpen'] = int(dev.states['brightnessLevel'])
                                # advancedOptionUi = ''
                                if (self.globals['trvc'][trvCtlrDevId]['advancedOption'] == ADVANCED_OPTION_FIRMWARE_WORKAROUND
                                        or self.globals['trvc'][trvCtlrDevId]['advancedOption'] == ADVANCED_OPTION_VALVE_ASSISTANCE):
                                    advancedOptionUi = ADVANCED_OPTION_UI[self.globals['trvc'][trvCtlrDevId]['advancedOption']]
                                    self.logger.debug(
                                        f'Found Valve device for \'{trvcDev.name}\': \'{dev.name}\' - Valve percentage open = {self.globals["trvc"][trvCtlrDevId]["valvePercentageOpen"]}% [{advancedOptionUi}]')

            else:
                # Work out how to handle this error situation !!!
                return

            self.globals['schedules'][trvCtlrDevId] = dict()
            self.globals['schedules'][trvCtlrDevId]['default'] = dict()  # setup from device configuration
            self.globals['schedules'][trvCtlrDevId]['running'] = dict()  # based on 'default' and potentially modified by change schedule actions
            self.globals['schedules'][trvCtlrDevId]['dynamic'] = dict()  # based on 'running' and potentially modified in response to Boost / Advance / Extend actions

            self.globals['trvc'][trvCtlrDevId]['remoteDevId'] = 0  # Assume no remote thermostat control
            self.globals['trvc'][trvCtlrDevId]['remoteThermostatControlEnabled'] = bool(trvcDev.pluginProps.get('remoteThermostatControlEnabled', False))
            if self.globals['trvc'][trvCtlrDevId]['remoteThermostatControlEnabled']:
                self.globals['trvc'][trvCtlrDevId]['remoteDevId'] = int(trvcDev.pluginProps.get('remoteDevId', 0))  # ID of Remote Thermostat device
                if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:

                    if indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].protocol == indigo.kProtocol.ZWave:
                        self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].address)] = dict()
                        self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].address)]['devId'] = self.globals['trvc'][trvCtlrDevId][
                            'remoteDevId']
                        self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].address)]['type'] = REMOTE
                        self.globals['zwave']['addressToDevice'][int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].address)]['trvcId'] = trvCtlrDevId
                        self.globals['zwave']['WatchList'].add(int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].address))

            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0 and self.globals['trvc'][trvCtlrDevId]['trvSupportsTemperatureReporting']:
                if trvcDev.pluginProps.get('NumTemperatureInputs', 0) != 2:
                    pluginProps = trvcDev.pluginProps
                    pluginProps["NumTemperatureInputs"] = 2
                    trvcDev.replacePluginPropsOnServer(pluginProps)
                    return
            else:
                if trvcDev.pluginProps.get('NumTemperatureInputs', 0) != 1:
                    pluginProps = trvcDev.pluginProps
                    pluginProps["NumTemperatureInputs"] = 1
                    trvcDev.replacePluginPropsOnServer(pluginProps)
                    return

            self.globals['trvc'][trvCtlrDevId]['trvSupportsHvacOperationMode'] = bool(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].supportsHvacOperationMode)
            self.logger.debug(
                f'TRV \'{indigo.devices[self.globals["trvc"][trvCtlrDevId]["trvDevId"]].name}\' supports HVAC Operation Mode = {self.globals["trvc"][trvCtlrDevId]["trvSupportsHvacOperationMode"]}')

            self.globals['trvc'][trvCtlrDevId]['heatingId'] = int(trvcDev.pluginProps.get('heatingId', 0))  # ID of Heat Source Controller device

            if self.globals['trvc'][trvCtlrDevId]['heatingId'] != 0 and self.globals['trvc'][trvCtlrDevId]['heatingId'] not in self.globals['heaterDevices'].keys():
                self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']] = dict()
                self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']][
                    'thermostatsCallingForHeat'] = set()  # A set of TRVs calling for heat from this heat source [None at the moment]

                self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['heaterControlType'] = HEAT_SOURCE_NOT_FOUND  # Default to No Heating Source

                dev = indigo.devices[self.globals['trvc'][trvCtlrDevId]['heatingId']]
                if 'hvacOperationMode' in dev.states:
                    self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['heaterControlType'] = HEAT_SOURCE_CONTROL_HVAC  # hvac
                    self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['onState'] = HEAT_SOURCE_INITIALISE
                elif 'onOffState' in dev.states:
                    self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['heaterControlType'] = HEAT_SOURCE_CONTROL_RELAY  # relay device
                    self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['onState'] = HEAT_SOURCE_INITIALISE
                else:
                    indigo.server.error(f'Error detected by TRV Plugin for device [{trvcDev.name}] - Unknown Heating Source Device Type with Id: {self.globals["trvc"][trvCtlrDevId]["heatingId"]}')

                if self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['heaterControlType'] != HEAT_SOURCE_NOT_FOUND:
                    self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_KEEP_HEAT_SOURCE_CONTROLLER_ALIVE, None, [self.globals['trvc'][trvCtlrDevId]['heatingId'], ]])

            self.globals['trvc'][trvCtlrDevId]['heatingVarId'] = int(trvcDev.pluginProps.get('heatingVarId', 0))  # ID of Heat Source Controller device

            if self.globals['trvc'][trvCtlrDevId]['heatingVarId'] != 0 and self.globals['trvc'][trvCtlrDevId]['heatingVarId'] not in self.globals['heaterVariables'].keys():
                self.globals['heaterVariables'][self.globals['trvc'][trvCtlrDevId]['heatingVarId']] = dict()
                self.globals['heaterVariables'][self.globals['trvc'][trvCtlrDevId]['heatingVarId']][
                    'thermostatsCallingForHeat'] = set()  # A set of TRVs calling for heat from this heat source [None at the moment]
                indigo.variable.updateValue(self.globals['trvc'][trvCtlrDevId]['heatingVarId'], value="false")  # Variable indicator to show that heating is NOT being requested

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
                self.globals['trvc'][trvCtlrDevId]['schedule1SetpointHeatUi'] = f'{self.globals["trvc"][trvCtlrDevId]["schedule1SetpointHeat"]} °C'
                self.globals['trvc'][trvCtlrDevId]['schedule1TimeUi'] = f'{self.globals["trvc"][trvCtlrDevId]["schedule1TimeOn"]} - {self.globals["trvc"][trvCtlrDevId]["schedule1TimeOff"]}'

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
                self.globals['trvc'][trvCtlrDevId]['schedule2SetpointHeatUi'] = f'{self.globals["trvc"][trvCtlrDevId]["schedule2SetpointHeat"]} °C'
                self.globals['trvc'][trvCtlrDevId]['schedule2TimeUi'] = f'{self.globals["trvc"][trvCtlrDevId]["schedule2TimeOn"]} - {self.globals["trvc"][trvCtlrDevId]["schedule2TimeOff"]}'

            self.globals['trvc'][trvCtlrDevId]['schedule3Enabled'] = bool(trvcDev.pluginProps.get('schedule3Enabled', False))
            if self.globals['trvc'][trvCtlrDevId]['schedule3Enabled']:
                self.globals['trvc'][trvCtlrDevId]['schedule3TimeOn'] = trvcDev.pluginProps.get('schedule3TimeOn', '00:00')
                self.globals['trvc'][trvCtlrDevId]['schedule3TimeOff'] = trvcDev.pluginProps.get('schedule3TimeOff', '00:00')
                self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeat'] = float(trvcDev.pluginProps.get('schedule3SetpointHeat', 0.0))
            else:
                self.globals['trvc'][trvCtlrDevId]['schedule3TimeOn'] = '00:00'
                self.globals['trvc'][trvCtlrDevId]['schedule3TimeOff'] = '00:00'
                self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeat'] = 0.0
            if not self.globals['trvc'][trvCtlrDevId]['schedule3Enabled'] or self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeat'] == 0.0:
                self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeatUi'] = 'Not Set'
                self.globals['trvc'][trvCtlrDevId]['schedule3TimeUi'] = 'Inactive'
            else:
                self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeatUi'] = f'{self.globals["trvc"][trvCtlrDevId]["schedule3SetpointHeat"]} °C'
                self.globals['trvc'][trvCtlrDevId]['schedule3TimeUi'] = f'{self.globals["trvc"][trvCtlrDevId]["schedule3TimeOn"]} - {self.globals["trvc"][trvCtlrDevId]["schedule3TimeOff"]}'

            self.globals['trvc'][trvCtlrDevId]['schedule4Enabled'] = bool(trvcDev.pluginProps.get('schedule4Enabled', False))
            if self.globals['trvc'][trvCtlrDevId]['schedule4Enabled']:
                self.globals['trvc'][trvCtlrDevId]['schedule4TimeOn'] = trvcDev.pluginProps.get('schedule4TimeOn', '00:00')
                self.globals['trvc'][trvCtlrDevId]['schedule4TimeOff'] = trvcDev.pluginProps.get('schedule4TimeOff', '00:00')
                self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeat'] = float(trvcDev.pluginProps.get('schedule4SetpointHeat', 0.0))
            else:
                self.globals['trvc'][trvCtlrDevId]['schedule4TimeOn'] = '00:00'
                self.globals['trvc'][trvCtlrDevId]['schedule4TimeOff'] = '00:00'
                self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeat'] = 0.0
            if not self.globals['trvc'][trvCtlrDevId]['schedule4Enabled'] or self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeat'] == 0.0:
                self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeatUi'] = 'Not Set'
                self.globals['trvc'][trvCtlrDevId]['schedule4TimeUi'] = 'Inactive'
            else:
                self.globals['trvc'][trvCtlrDevId]['schedule4SetpointHeatUi'] = f'{self.globals["trvc"][trvCtlrDevId]["schedule4SetpointHeat"]} °C'
                self.globals['trvc'][trvCtlrDevId]['schedule4TimeUi'] = f'{self.globals["trvc"][trvCtlrDevId]["schedule4TimeOn"]} - {self.globals["trvc"][trvCtlrDevId]["schedule4TimeOff"]}'

            # Following section of code is to save the values if the schedule is reset to as defined in the device configuration
            self.globals['trvc'][trvCtlrDevId]['scheduleReset1Enabled'] = self.globals['trvc'][trvCtlrDevId]['schedule1Enabled']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset1TimeOn'] = self.globals['trvc'][trvCtlrDevId]['schedule1TimeOn']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset1TimeOff'] = self.globals['trvc'][trvCtlrDevId]['schedule1TimeOff']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset1TimeUi'] = self.globals['trvc'][trvCtlrDevId]['schedule1TimeUi']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset1HeatSetpoint'] = self.globals['trvc'][trvCtlrDevId]['schedule1SetpointHeat']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset2Enabled'] = self.globals['trvc'][trvCtlrDevId]['schedule2Enabled']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset2TimeOn'] = self.globals['trvc'][trvCtlrDevId]['schedule2TimeOn']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset2TimeOff'] = self.globals['trvc'][trvCtlrDevId]['schedule2TimeOff']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset2TimeUi'] = self.globals['trvc'][trvCtlrDevId]['schedule2TimeUi']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset2HeatSetpoint'] = self.globals['trvc'][trvCtlrDevId]['schedule2SetpointHeat']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset3Enabled'] = self.globals['trvc'][trvCtlrDevId]['schedule3Enabled']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset3TimeOn'] = self.globals['trvc'][trvCtlrDevId]['schedule3TimeOn']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset3TimeOff'] = self.globals['trvc'][trvCtlrDevId]['schedule3TimeOff']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset3TimeUi'] = self.globals['trvc'][trvCtlrDevId]['schedule3TimeUi']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset3HeatSetpoint'] = self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeat']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset4Enabled'] = self.globals['trvc'][trvCtlrDevId]['schedule4Enabled']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset4TimeOn'] = self.globals['trvc'][trvCtlrDevId]['schedule4TimeOn']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset4TimeOff'] = self.globals['trvc'][trvCtlrDevId]['schedule4TimeOff']
            self.globals['trvc'][trvCtlrDevId]['scheduleReset4TimeUi'] = self.globals['trvc'][trvCtlrDevId]['schedule4TimeUi']
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

            self.globals['trvc'][trvCtlrDevId]['deviceStartDatetime'] = str(currentTime)

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
                self.logger.info(f'\'{trvcDev.name}\' Heat Setpoint set to device minimum value i.e. \'{self.globals["trvc"][trvCtlrDevId]["setpointHeat"]}\'')
            elif self.globals['trvc'][trvCtlrDevId]['setpointHeatDeviceStartMethod'] == DEVICE_START_SETPOINT_LEAVE_AS_IS:
                self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(indigo.devices[trvCtlrDevId].heatSetpoint)
                self.logger.info(f'\'{trvcDev.name}\' Heat Setpoint left unchanged i.e. \'{self.globals["trvc"][trvCtlrDevId]["setpointHeat"]}\'')
            elif self.globals['trvc'][trvCtlrDevId]['setpointHeatDeviceStartMethod'] == DEVICE_START_SETPOINT_SPECIFIED:
                self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatDeviceStartDefault'])
                self.logger.info(f'\'{trvcDev.name}\' Heat Setpoint set to specified \'Device Start\' value i.e. \'{self.globals["trvc"][trvCtlrDevId]["setpointHeat"]}\'')
            else:
                self.logger.error(
                    f'Error detected by TRV Plugin for device [{trvcDev.name}] - Unknown method \'{self.globals["trvc"][trvCtlrDevId]["setpointHeatDeviceStartMethod"]}\' to set Device Start Heat Setpoint')
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

            self.globals['trvc'][trvCtlrDevId]['modeDatetimeChanged'] = currentTime

            if self.globals['trvc'][trvCtlrDevId]['trvSupportsTemperatureReporting']:
                self.globals['trvc'][trvCtlrDevId]['temperatureTrv'] = float(indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['trvDevId'])].temperatures[0])
            else:
                self.globals['trvc'][trvCtlrDevId]['temperatureTrv'] = 0.0

            self.globals['trvc'][trvCtlrDevId]['temperatureRemote'] = float(0.0)
            self.globals['trvc'][trvCtlrDevId]['temperatureRemotePreOffset'] = float(0.0)
            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:
                try:
                    self.globals['trvc'][trvCtlrDevId]['temperatureRemote'] = float(
                        indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['remoteDevId'])].temperatures[0])  # e.g. Radiator Thermostat (HRT4-ZW)
                except AttributeError:
                    try:
                        self.globals['trvc'][trvCtlrDevId]['temperatureRemote'] = float(
                            indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['remoteDevId'])].states['sensorValue'])  # e.g. Aeon 4 in 1 / Fibaro FGMS-001
                    except (AttributeError, KeyError):
                        try:
                            self.globals['trvc'][trvCtlrDevId]['temperatureRemote'] = float(
                                indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['remoteDevId'])].states['temperature'])  # e.g. Oregon Scientific Temp Sensor
                        except (AttributeError, KeyError):
                            try:
                                self.globals['trvc'][trvCtlrDevId]['temperatureRemote'] = float(
                                    indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['remoteDevId'])].states['Temperature'])  # e.g. Netatmo
                            except (AttributeError, KeyError):
                                indigo.server.error(
                                    f'\'{indigo.devices[self.globals["trvc"][trvCtlrDevId]["remoteDevId"]].name}\' is an unknown Remote Thermostat type - Remote support disabled for TRV \'{trvcDev.name}\'')
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
                    except Exception:
                        self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl'] = False

            self.globals['trvc'][trvCtlrDevId]['zwaveEventWakeUpSentDisplayFix'] = ''  # Used to flip the Z-wave reporting around for Wakeup command (Indigo fix)
            self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountTrv'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountPreviousTrv'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwaveSentCountTrv'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwaveSentCountPreviousTrv'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeTrv'] = 'N/A'
            self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeTrv'] = 'N/A'
            self.globals['trvc'][trvCtlrDevId]['zwaveWakeupDelayTrv'] = False
            self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'] = int(
                indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].globalProps["com.perceptiveautomation.indigoplugin.zwave"]["zwWakeInterval"])

            if self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'] > 0:
                trvDevId = self.globals['trvc'][trvCtlrDevId]['trvDevId']
                nextWakeupMissedSeconds = (self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'] + 2) * 60  # Add 2 minutes to next expected wakeup
                if trvDevId in self.globals['timers']['zwaveWakeupCheck']:
                    self.globals['timers']['zwaveWakeupCheck'][trvDevId].cancel()
                self.globals['timers']['zwaveWakeupCheck'][trvDevId] = threading.Timer(float(nextWakeupMissedSeconds), self.zwaveWakeupMissedTriggered, [trvCtlrDevId, TRV, trvDevId])
                self.globals['timers']['zwaveWakeupCheck'][trvDevId].daemon = True
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
                            self.globals['timers']['zwaveWakeupCheck'][remoteDevId] = threading.Timer(float(nextWakeupMissedSeconds), self.zwaveWakeupMissedTriggered,
                                                                                                      [trvCtlrDevId, REMOTE, remoteDevId])
                            self.globals['timers']['zwaveWakeupCheck'][remoteDevId].daemon = True
                            self.globals['timers']['zwaveWakeupCheck'][remoteDevId].start()
                    except Exception:
                        self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalRemote'] = int(0)
                else:
                    # self.logger.debug("Protocol for device %s is '%s'" % (indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].name, indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].protocol))
                    self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalRemote'] = int(0)

            self.globals['trvc'][trvCtlrDevId]['zwaveLastSentCommandRemote'] = ''
            self.globals['trvc'][trvCtlrDevId]['zwaveLastReceivedCommandRemote'] = ''
            self.globals['trvc'][trvCtlrDevId]['zwavePendingHvac'] = False  # Used to differentiate between internally generated Z-Wave hvac command and UI generated Z-Wave hvac commands

            self.globals['trvc'][trvCtlrDevId][
                'zwavePendingTrvSetpointFlag'] = False  # Used to differentiate between internally generated Z-Wave setpoint command and UI generated Z-Wave setpoint commands
            self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointSequence'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointValue'] = 0.0
            self.globals['trvc'][trvCtlrDevId][
                'zwavePendingRemoteSetpointFlag'] = False  # Used to differentiate between internally generated Z-Wave setpoint command and UI generated Z-Wave setpoint commands
            self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointSequence'] = 0
            self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointValue'] = 0.0

            self.globals['trvc'][trvCtlrDevId]['deltaIncreaseHeatSetpoint'] = 0.0
            self.globals['trvc'][trvCtlrDevId]['deltaIDecreaseHeatSetpoint'] = 0.0

            self.globals['trvc'][trvCtlrDevId]['callingForHeat'] = False
            self.globals['trvc'][trvCtlrDevId]['callingForHeatTrueSSM'] = 0  # Calling For Heat True Seconds Since Midnight
            self.globals['trvc'][trvCtlrDevId]['callingForHeatFalseSSM'] = 0  # Calling For Heat False Seconds Since Midnight

            # Update device states

            keyValueList = [{'key': 'hvacOperationMode', 'value': self.globals['trvc'][trvCtlrDevId]['hvacOperationMode']},
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
                            {'key': 'boostActive', 'value': self.globals['trvc'][trvCtlrDevId]['boostActive']}, {'key': 'boostMode', 'value': self.globals['trvc'][trvCtlrDevId]['boostMode']},
                            {'key': 'boostModeUi', 'value': self.globals['trvc'][trvCtlrDevId]['boostModeUi']}, {'key': 'boostStatusUi', 'value': self.globals['trvc'][trvCtlrDevId]['boostStatusUi']},
                            {'key': 'boostDeltaT', 'value': self.globals['trvc'][trvCtlrDevId]['boostDeltaT']},
                            {'key': 'boostSetpoint', 'value': int(self.globals['trvc'][trvCtlrDevId]['boostSetpoint'])},
                            {'key': 'boostMinutes', 'value': self.globals['trvc'][trvCtlrDevId]['boostMinutes']},
                            {'key': 'boostTimeStart', 'value': self.globals['trvc'][trvCtlrDevId]['boostTimeStart']},
                            {'key': 'boostTimeEnd', 'value': self.globals['trvc'][trvCtlrDevId]['boostTimeEnd']}, {'key': 'extendActive', 'value': self.globals['trvc'][trvCtlrDevId]['extendActive']},
                            {'key': 'extendStatusUi', 'value': self.globals['trvc'][trvCtlrDevId]['extendStatusUi']},
                            {'key': 'extendMinutes', 'value': self.globals['trvc'][trvCtlrDevId]['extendMinutes']},
                            {'key': 'extendActivatedTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendActivatedTime']},
                            {'key': 'extendScheduleOriginalTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendScheduleOriginalTime']},
                            {'key': 'extendScheduleNewTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendScheduleNewTime']},
                            {'key': 'extendLimitReached', 'value': self.globals['trvc'][trvCtlrDevId]['extendLimitReached']},
                            {'key': 'callingForHeat', 'value': self.globals['trvc'][trvCtlrDevId]['callingForHeat']},
                            {'key': 'callingForHeatTrueSSM', 'value': self.globals['trvc'][trvCtlrDevId]['callingForHeatTrueSSM']},
                            {'key': 'callingForHeatFalseSSM', 'value': self.globals['trvc'][trvCtlrDevId]['callingForHeatFalseSSM']},
                            {'key': 'eventReceivedDateTimeRemote', 'value': self.globals['trvc'][trvCtlrDevId]['lastSuccessfulCommRemote']},
                            {'key': 'zwaveEventReceivedDateTimeTrv', 'value': self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeTrv']},
                            {'key': 'zwaveEventReceivedDateTimeRemote', 'value': self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeRemote']},
                            {'key': 'zwaveEventSentDateTimeTrv', 'value': self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeTrv']},
                            {'key': 'zwaveEventSentDateTimeRemote', 'value': self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeRemote']},
                            {'key': 'valvePercentageOpen', 'value': self.globals['trvc'][trvCtlrDevId]['valvePercentageOpen']}, {'key': 'hvacHeaterIsOn', 'value': False},
                            {'key': 'setpointHeat', 'value': self.globals['trvc'][trvCtlrDevId]['setpointHeat']},
                            dict(key='batteryLevel', value=int(self.globals['trvc'][trvCtlrDevId]['batteryLevel']), uiValue=f'{self.globals["trvc"][trvCtlrDevId]["batteryLevel"]}%'),
                            dict(key='batteryLevelTrv', value=int(self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv']), uiValue=f'{self.globals["trvc"][trvCtlrDevId]["batteryLevelTrv"]}%'),
                            dict(key='batteryLevelRemote', value=int(self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote']),
                                 uiValue=f'{self.globals["trvc"][trvCtlrDevId]["batteryLevelRemote"]}%'),
                            {'key': 'hvacOperationModeTrv', 'value': self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv']},
                            {'key': 'hvacOperationMode', 'value': self.globals['trvc'][trvCtlrDevId]['hvacOperationMode']},
                            {'key': 'controllerMode', 'value': self.globals['trvc'][trvCtlrDevId]['controllerMode']},
                            {'key': 'controllerModeUi', 'value': CONTROLLER_MODE_TRANSLATION[self.globals['trvc'][trvCtlrDevId]['controllerMode']]},
                            {'key': 'temperatureInput1', 'value': self.globals['trvc'][trvCtlrDevId]['temperature'], 'uiValue': f'{self.globals["trvc"][trvCtlrDevId]["temperature"]:.1f} °C'}]

            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:
                if self.globals['trvc'][trvCtlrDevId]['trvSupportsTemperatureReporting']:
                    keyValueList.append({'key': 'temperatureInput2', 'value': self.globals['trvc'][trvCtlrDevId]['temperatureTrv'],
                                         'uiValue': f'{self.globals["trvc"][trvCtlrDevId]["temperatureTrv"]:.1f} °C'})
                    keyValueList.append({'key': 'temperatureUi',
                                         'value': f'R: {self.globals["trvc"][trvCtlrDevId]["temperatureRemote"]:.1f} °C, T: {self.globals["trvc"][trvCtlrDevId]["temperatureTrv"]:.1f} °C'})
                else:
                    keyValueList.append({'key': 'temperatureUi', 'value': f'R: {self.globals["trvc"][trvCtlrDevId]["temperatureRemote"]:.1f} °C'})

            else:
                keyValueList.append({'key': 'temperatureUi', 'value': f'T: {self.globals["trvc"][trvCtlrDevId]["temperatureTrv"]:.1f} °C'})

            trvcDev.updateStatesOnServer(keyValueList)

            trvcDev.updateStateImageOnServer(indigo.kStateImageSel.HvacAutoMode)  # HvacOff - HvacHeatMode - HvacHeating - HvacAutoMode

            # Check if CSV Files need initialising

            if self.globals['trvc'][trvCtlrDevId]['updateCsvFile']:
                if self.globals['trvc'][trvCtlrDevId]['updateAllCsvFiles']:
                    self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES, trvCtlrDevId, None])
                else:
                    self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['setpointHeat', float(self.globals['trvc'][trvCtlrDevId]['setpointHeat'])]])
                    self.globals['queues']['trvHandler'].put(
                        [QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['temperatureTrv', float(self.globals['trvc'][trvCtlrDevId]['temperatureTrv'])]])
                    self.globals['queues']['trvHandler'].put(
                        [QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['setpointHeatTrv', float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])]])
                    if self.globals['trvc'][trvCtlrDevId]['valveDevId'] != 0:
                        self.globals['queues']['trvHandler'].put(
                            [QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['valvePercentageOpen', int(self.globals['trvc'][trvCtlrDevId]['valvePercentageOpen'])]])
                    if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:
                        self.globals['queues']['trvHandler'].put(
                            [QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['temperatureRemote', float(self.globals['trvc'][trvCtlrDevId]['temperatureRemote'])]])
                        if self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl']:
                            self.globals['queues']['trvHandler'].put(
                                [QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['setpointHeatRemote', float(self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote'])]])

            # Set-up schedules
            scheduleSetpointOff = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum'])
            self.globals['schedules'][trvCtlrDevId]['default'][0] = ('00:00', scheduleSetpointOff, 0, False)  # Start of Day
            self.globals['schedules'][trvCtlrDevId]['default'][240000] = ('24:00', scheduleSetpointOff, 9, False)  # End of Day

            if self.globals['trvc'][trvCtlrDevId]['schedule1Enabled']:
                scheduleTimeOnUi = self.globals['trvc'][trvCtlrDevId]['schedule1TimeOn']
                scheduleTimeOn = int(scheduleTimeOnUi.replace(':', '')) * 100  # Add in Seconds
                scheduleTimeOffUi = self.globals['trvc'][trvCtlrDevId]['schedule1TimeOff']
                scheduleTimeOff = int(scheduleTimeOffUi.replace(':', '')) * 100  # Add in Seconds
                scheduleSetpointOn = float(self.globals['trvc'][trvCtlrDevId]['schedule1SetpointHeat'])
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 1, True)
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 1, False)

            if self.globals['trvc'][trvCtlrDevId]['schedule2Enabled']:
                scheduleTimeOnUi = self.globals['trvc'][trvCtlrDevId]['schedule2TimeOn']
                scheduleTimeOn = int(scheduleTimeOnUi.replace(':', '')) * 100  # Add in Seconds
                scheduleTimeOffUi = self.globals['trvc'][trvCtlrDevId]['schedule2TimeOff']
                scheduleTimeOff = int(scheduleTimeOffUi.replace(':', '')) * 100  # Add in Seconds
                scheduleSetpointOn = float(self.globals['trvc'][trvCtlrDevId]['schedule2SetpointHeat'])
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 2, True)
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 2, False)

            if self.globals['trvc'][trvCtlrDevId]['schedule3Enabled']:
                scheduleTimeOnUi = self.globals['trvc'][trvCtlrDevId]['schedule3TimeOn']
                scheduleTimeOn = int(scheduleTimeOnUi.replace(':', '')) * 100  # Add in Seconds
                scheduleTimeOffUi = self.globals['trvc'][trvCtlrDevId]['schedule3TimeOff']
                scheduleTimeOff = int(scheduleTimeOffUi.replace(':', '')) * 100  # Add in Seconds
                scheduleSetpointOn = float(self.globals['trvc'][trvCtlrDevId]['schedule3SetpointHeat'])
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 3, True)
                self.globals['schedules'][trvCtlrDevId]['default'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 3, False)

            if self.globals['trvc'][trvCtlrDevId]['schedule4Enabled']:
                scheduleTimeOnUi = self.globals['trvc'][trvCtlrDevId]['schedule4TimeOn']
                scheduleTimeOn = int(scheduleTimeOnUi.replace(':', '')) * 100  # Add in Seconds
                scheduleTimeOffUi = self.globals['trvc'][trvCtlrDevId]['schedule4TimeOff']
                scheduleTimeOff = int(scheduleTimeOffUi.replace(':', '')) * 100  # Add in Seconds
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
                heatingId = int(self.globals['trvc'][trvCtlrDevId]['heatingId'])
                if heatingId == 0:
                    heatingDeviceUi = 'No Device Heat Source control required.'
                else:
                    heatingDeviceUi = f'Device Heat Source \'{indigo.devices[int(self.globals["trvc"][trvCtlrDevId]["heatingId"])].name}\''

                heatingVarId = int(self.globals['trvc'][trvCtlrDevId]['heatingVarId'])
                if heatingVarId == 0:
                    heatingVarUi = 'No Variable Heat Source control required.'
                else:
                    heatingVarUi = f'Variable Heat Source \'{indigo.variables[int(self.globals["trvc"][trvCtlrDevId]["heatingVarId"])].name}\''

                if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] == 0:
                    if not self.globals['trvc'][trvCtlrDevId]['trvSupportsTemperatureReporting']:
                        self.logger.error(f'TRV Controller can\'t control TRV \'{trvcDev.name}\' as the TRV does not report temperature and there is no Remote Stat defined!')
                        self.globals['trvc'][trvCtlrDevId]['deviceStarted'] = True
                        return
                    else:
                        self.logger.info(f'Started \'{trvcDev.name}\': Controlling TRV \'{indigo.devices[int(self.globals["trvc"][trvCtlrDevId]["trvDevId"])].name}\';\n{heatingDeviceUi}')
                else:
                    self.logger.info(f'Started \'{trvcDev.name}\': Controlling TRV \'{indigo.devices[int(self.globals["trvc"][trvCtlrDevId]["trvDevId"])].name}\'; '
                                     f'Remote thermostat \'{indigo.devices[int(self.globals["trvc"][trvCtlrDevId]["remoteDevId"])].name}\'; {heatingDeviceUi};\n{heatingVarUi}')

                self.globals['trvc'][trvCtlrDevId]['deviceStarted'] = True
                self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_DELAY_COMMAND, trvCtlrDevId, [CMD_PROCESS_HEATING_SCHEDULE, 2.0, None]])

            except Exception as exception_error:
                self.exception_handler(exception_error, True)  # Log error and display failing statement

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def deviceStopComm(self, trvcDev):
        try:

            trvCtlrDevId = trvcDev.id

            if not self.globals['trvc'][trvCtlrDevId]['deviceStarted']:
                self.logger.debug(f'controlTrv: \'{trvcDev.name}\' device stopping but startup not yet completed')

            self.globals['trvc'][trvCtlrDevId]['deviceStarted'] = False

            if 'trvDevId' in self.globals['trvc'][trvCtlrDevId] and self.globals['trvc'][trvCtlrDevId]['trvDevId'] != 0:
                self.globals['zwave']['WatchList'].discard(int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].address))
            if 'remoteDevId' in self.globals['trvc'][trvCtlrDevId] and self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:

                if indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].protocol == indigo.kProtocol.ZWave:
                    self.globals['zwave']['WatchList'].discard(int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['remoteDevId']].address))
            self.logger.info(f"Stopping '{trvcDev.name}'")
        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def deviceUpdated(self, origDev, newDev):

        def secondsSinceMidnight():
            utcnow = datetime.datetime.utcnow()
            midnight_utc = datetime.datetime.combine(utcnow.date(), datetime.time(0))
            delta = utcnow - midnight_utc
            return int(delta.seconds)

        try:
            def check_for_race_condition(device_key, device_name, device_description):
                race_condition = False
                race_seconds = secondsSinceMidnight()
                if self.globals['trvc'][trvCtlrDevId]['raceConditionDetector'][device_key]['updateSecondsSinceMidnight'] != race_seconds:
                    self.globals['trvc'][trvCtlrDevId]['raceConditionDetector'][device_key]['updateSecondsSinceMidnight'] = race_seconds
                    self.globals['trvc'][trvCtlrDevId]['raceConditionDetector'][device_key]['updatesInLastSecond'] = 1
                    self.logger.threaddebug(f'=======> RACE DETECTION FOR {device_name} \'{newDev.name}\': SECONDS SINCE MIDNIGHT = \'{race_seconds}\', COUNT RESET TO 1')
                else:
                    self.globals['trvc'][trvCtlrDevId]['raceConditionDetector'][device_key]['updatesInLastSecond'] += 1
                    if self.globals['trvc'][trvCtlrDevId]['raceConditionDetector'][device_key]['updatesInLastSecond'] > \
                            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector'][device_key]['updatesInLastSecondMaximum']:
                        self.globals['trvc'][trvCtlrDevId]['raceConditionDetector'][device_key]['updatesInLastSecondMaximum'] = \
                            self.globals['trvc'][trvCtlrDevId]['raceConditionDetector'][device_key]['updatesInLastSecond']
                    self.logger.threaddebug(
                        f'=======> RACE DETECTION FOR {device_name} \'{newDev.name}\': SECONDS SINCE MIDNIGHT = \'{race_seconds}\', COUNT = \'{self.globals["trvc"][trvCtlrDevId]["raceConditionDetector"][device_key]["updatesInLastSecond"]}\' [MAX = \'{self.globals["trvc"][trvCtlrDevId]["raceConditionDetector"][device_key]["updatesInLastSecondMaximum"]}\'] <=======')
                    if self.globals['trvc'][trvCtlrDevId]['raceConditionDetector'][device_key]['updatesInLastSecond'] > RACE_CONDITION_LIMIT:
                        self.logger.error(
                            f'Potential race condition detected for {device_description} \'{newDev.name}\' in TRV Plugin [deviceUpdated] - TRV Controller device being disabled for 60 seconds!')
                        indigo.device.enable(trvCtlrDevId, value=False)

                        # setting a timer to re-enable after 60 seconds

                        self.globals['timers']['raceCondition'][trvCtlrDevId] = threading.Timer(60.0, self.deviceRaceConditionReEnableTriggered, [trvCtlrDevId])
                        self.globals['timers']['raceCondition'][trvCtlrDevId].daemon = True
                        self.globals['timers']['raceCondition'][trvCtlrDevId].start()

                        race_condition = True

                return race_condition  # Note if True then the 'finally:' statement at the end of deviceUpdated method will return the correct values to Indigo

            device_updated_prefix = f"{u'':={u'^'}22}> "  # 22 equal signs as first part of prefix

            if (newDev.deviceTypeId == 'trvController' and newDev.configured and newDev.id in self.globals['trvc']
                    and self.globals['trvc'][newDev.id]['deviceStarted']):

                # As this is a TRV Controller device only log the updates - Don't queue the update for the TRV Handler otherwise it will loop!

                trvCtlrDevId = newDev.id

                # Check for Race condition
                race_condition_result = check_for_race_condition("trvController", "TRV CONTROLLER", "TRV Controller")
                if race_condition_result:
                    return  # Note that the 'finally:' statement at the end of this deviceUpdated method will return the correct values to Indigo

                self.globals['trvc'][trvCtlrDevId]['lastSuccessfulComm'] = newDev.lastSuccessfulComm

                updateLogItems = list()

                if origDev.hvacMode != newDev.hvacMode:
                    oldInternalHvacMode = self.globals['trvc'][trvCtlrDevId]['hvacOperationMode']
                    self.globals['trvc'][trvCtlrDevId]['hvacOperationMode'] = newDev.hvacMode
                    updateLogItems.append(
                        f'HVAC Operation Mode updated from {HVAC_TRANSLATION[origDev.hvacMode]} to {HVAC_TRANSLATION[newDev.hvacMode]} [Internal store was = {HVAC_TRANSLATION[oldInternalHvacMode]} and is now = {HVAC_TRANSLATION[int(self.globals["trvc"][trvCtlrDevId]["hvacOperationMode"])]}]')

                if (float(origDev.temperatures[0]) != float(newDev.temperatures[0])) or (self.globals['trvc'][trvCtlrDevId]['temperature'] != float(newDev.temperatures[0])):
                    origTemp = float(origDev.temperatures[0])
                    newTemp = float(newDev.temperatures[0])
                    updateLogItems.append(f'Temperature updated from {origTemp} to {newTemp} [Internal store = {self.globals["trvc"][trvCtlrDevId]["temperature"]}]')

                if origDev.states['controllerMode'] != newDev.states['controllerMode']:
                    oldInternalControllerMode = self.globals['trvc'][trvCtlrDevId]['controllerMode']
                    self.globals['trvc'][trvCtlrDevId]['controllerMode'] = newDev.states['controllerMode']
                    updateLogItems.append(
                        f'Mode updated from {CONTROLLER_MODE_TRANSLATION[origDev.states["controllerMode"]]} to {CONTROLLER_MODE_TRANSLATION[newDev.states["controllerMode"]]} [Internal store was = {CONTROLLER_MODE_TRANSLATION[oldInternalControllerMode]} and is now = {CONTROLLER_MODE_TRANSLATION[self.globals["trvc"][trvCtlrDevId]["controllerMode"]]}]')

                if float(origDev.heatSetpoint) != float(newDev.heatSetpoint):
                    oldInternalSetpointHeat = self.globals['trvc'][trvCtlrDevId]['setpointHeat']
                    self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(newDev.heatSetpoint)
                    updateLogItems.append(
                        f'Heat Setpoint changed from {origDev.heatSetpoint} to {newDev.heatSetpoint} [Internal store was = {oldInternalSetpointHeat} and is now = {self.globals["trvc"][trvCtlrDevId]["setpointHeat"]}]')

                    # Update CSV files if TRV Controller Heat Setpoint updated
                    if self.globals['trvc'][trvCtlrDevId]['updateCsvFile']:
                        if self.globals['trvc'][trvCtlrDevId]['updateAllCsvFiles']:
                            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES, trvCtlrDevId, None])
                        else:
                            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['setpointHeat', self.globals['trvc'][trvCtlrDevId]['setpointHeat']]])

                if len(updateLogItems) > 0:
                    device_updated_report = (
                        f"\n\n{device_updated_prefix}DEVICE UPDATED [{self.globals['deviceUpdatedSequenceCount']}]: TRV Controller '{newDev.name}'; Last Communication at {newDev.lastSuccessfulComm}\n")
                    for itemToReport in updateLogItems:
                        device_updated_report = (f"{device_updated_report}{device_updated_prefix}{itemToReport}\n")
                        self.logger.debug(device_updated_report)

            elif int(newDev.id) in self.globals['devicesToTrvControllerTable'].keys():  # Check if a TRV device or Remote Thermostat already stored in table

                deviceUpdatedLog = u'\n\n======================================================================================================================================================\n=='
                deviceUpdatedLog = deviceUpdatedLog + u'\n==  Method: \'deviceUpdated\''
                self.globals['deviceUpdatedSequenceCount'] += 1
                deviceUpdatedLog = deviceUpdatedLog + f'\n==  Sequence: {self.globals["deviceUpdatedSequenceCount"]}'
                deviceUpdatedLog = deviceUpdatedLog + f'\n==  Device: {DEVICE_TYPE_TRANSLATION[self.globals["devicesToTrvControllerTable"][newDev.id]["type"]]} - \'{newDev.name}\''
                deviceUpdatedLog = deviceUpdatedLog + f'\n==  Last Communication: {newDev.lastSuccessfulComm}'

                trvCtlrDevId = int(self.globals['devicesToTrvControllerTable'][newDev.id]['trvControllerId'])

                if indigo.devices[trvCtlrDevId].enabled:

                    trvControllerDev = indigo.devices[trvCtlrDevId]

                    updateRequested = False

                    updateList = dict()

                    updateLogItems = dict()

                    if self.globals['devicesToTrvControllerTable'][newDev.id]['type'] == TRV or self.globals['devicesToTrvControllerTable'][newDev.id]['type'] == VALVE:

                        race_condition_result = check_for_race_condition("trv", "TRV", "TRV device managed by TRV Controller")
                        if race_condition_result:
                            return  # Note that the 'finally:' statement at the end of this deviceUpdated method will return the correct values to Indigo

                        # The first checks are general across all sub-devices i.e thermostat and valve

                        self.globals['trvc'][trvCtlrDevId]['lastSuccessfulCommTrv'] = newDev.lastSuccessfulComm

                        # Check if Z-Wave Event has been received
                        if self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountTrv'] > self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountPreviousTrv']:
                            self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountPreviousTrv'] = self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountTrv']
                            updateRequested = True
                            updateList[UPDATE_ZWAVE_EVENT_RECEIVED_TRV] = self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeTrv']
                            updateLogItems[UPDATE_ZWAVE_EVENT_RECEIVED_TRV] = f'TRV Z-Wave event received. Time updated to \'{self.globals["trvc"][trvCtlrDevId]["zwaveEventReceivedDateTimeTrv"]}\'. Received count now totals: {self.globals["trvc"][trvCtlrDevId]["zwaveReceivedCountTrv"]}'

                        # Check if Z-Wave Event has been sent
                        if self.globals['trvc'][trvCtlrDevId]['zwaveSentCountTrv'] > self.globals['trvc'][trvCtlrDevId]['zwaveSentCountPreviousTrv']:
                            self.globals['trvc'][trvCtlrDevId]['zwaveSentCountPreviousTrv'] = self.globals['trvc'][trvCtlrDevId]['zwaveSentCountTrv']
                            updateRequested = True
                            updateList[UPDATE_ZWAVE_EVENT_SENT_TRV] = self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeTrv']
                            updateLogItems[UPDATE_ZWAVE_EVENT_SENT_TRV] = f'TRV Z-Wave event sent. Time updated to \'{self.globals["trvc"][trvCtlrDevId]["zwaveEventSentDateTimeTrv"]}\'. Sent count now totals: {self.globals["trvc"][trvCtlrDevId]["zwaveSentCountTrv"]}'

                        # Check the wakeup interval in case it has changed
                        wakeupInterval = int(indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].globalProps["com.perceptiveautomation.indigoplugin.zwave"]["zwWakeInterval"])
                        if int(self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv']) != wakeupInterval:
                            self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'] = wakeupInterval
                            updateRequested = True
                            updateList[UPDATE_ZWAVE_WAKEUP_INTERVAL] = wakeupInterval
                            updateLogItems[UPDATE_ZWAVE_WAKEUP_INTERVAL] = f'TRV Z-Wave wakeup interval changed from \'{self.globals["trvc"][trvCtlrDevId]["zwaveWakeupIntervalTrv"]}\' to \'{wakeupInterval}\''

                        # if newDev.globalProps['com.perceptiveautomation.indigoplugin.zwave']['zwDevSubIndex'] == 0:  # Thermostat
                        if self.globals['devicesToTrvControllerTable'][newDev.id]['type'] == TRV:

                            if trvControllerDev.states['controllerMode'] != self.globals['trvc'][trvCtlrDevId]['controllerMode']:
                                updateRequested = True
                                updateList[UPDATE_CONTROLLER_MODE] = self.globals['trvc'][trvCtlrDevId]['controllerMode']
                                updateLogItems[UPDATE_CONTROLLER_MODE] = (
                                    f'Controller Mode updated from {CONTROLLER_MODE_TRANSLATION[trvControllerDev.states["controllerMode"]]} to {CONTROLLER_MODE_TRANSLATION[self.globals["trvc"][trvCtlrDevId]["controllerMode"]]}')

                            if 'batteryLevel' in newDev.states:
                                # self.logger.debug(f'=====================>>>> Battery Level for TRV device \'{origDev.name}\' - OLD: {origDev.batteryLevel}, NEW: {newDev.batteryLevel}')
                                if (origDev.batteryLevel != newDev.batteryLevel) or (self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'] != newDev.batteryLevel):
                                    self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'] = newDev.batteryLevel
                                    updateRequested = True
                                    updateList[UPDATE_TRV_BATTERY_LEVEL] = newDev.batteryLevel
                                    updateLogItems[UPDATE_TRV_BATTERY_LEVEL] = (
                                        f'TRV Battery Level updated from {origDev.batteryLevel} to {newDev.batteryLevel} [Internal store was = \'{self.globals["trvc"][trvCtlrDevId]["batteryLevelTrv"]}\']')

                            if self.globals['trvc'][trvCtlrDevId]['trvSupportsTemperatureReporting']:
                                if (float(origDev.temperatures[0]) != float(newDev.temperatures[0])) or (self.globals['trvc'][trvCtlrDevId]['temperatureTrv'] != float(newDev.temperatures[0])):
                                    origTemp = float(origDev.temperatures[0])
                                    newTemp = float(newDev.temperatures[0])
                                    updateRequested = True
                                    updateList[UPDATE_TRV_TEMPERATURE] = newTemp
                                    updateLogItems[UPDATE_TRV_TEMPERATURE] = (
                                        f'Temperature updated from {origTemp} to {newTemp} [Internal store was = \'{self.globals["trvc"][trvCtlrDevId]["temperatureTrv"]}\']')

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
                                    updateLogItems[UPDATE_TRV_HVAC_OPERATION_MODE] = f'TRV HVAC Operation Mode updated from \'{HVAC_TRANSLATION[origDev.hvacMode]}\' to \'{HVAC_TRANSLATION[newDev.hvacMode]}\' [Internal store was = \'{HVAC_TRANSLATION[int(self.globals["trvc"][trvCtlrDevId]["hvacOperationModeTrv"])]}\']'
                                else:
                                    updateLogItems[
                                        UPDATE_TRV_HVAC_OPERATION_MODE] = f'TRV HVAC Operation Mode update from \'{HVAC_TRANSLATION[origDev.hvacMode]}\' to \'{HVAC_TRANSLATION[newDev.hvacMode]}\', overridden and reset to \'{HVAC_TRANSLATION[hvacMode]}\' [Internal store was = \'{HVAC_TRANSLATION[self.globals["trvc"][trvCtlrDevId]["hvacOperationModeTrv"]]}\']'

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
                                            updateLogItems[UPDATE_ZWAVE_HVAC_OPERATION_MODE_ID] = f'ZWave HVAC Operation Mode updated from \'{HVAC_TRANSLATION[origDev.states["zwaveHvacOperationModeID"]]}\' to \'{HVAC_TRANSLATION[newDev.states["zwaveHvacOperationModeID"]]}\''
                                        else:
                                            updateLogItems[UPDATE_ZWAVE_HVAC_OPERATION_MODE_ID] = f'ZWave HVAC Operation Mode update from \'{HVAC_TRANSLATION[origDev.states["zwaveHvacOperationModeID"]]}\' to \'{HVAC_TRANSLATION[newDev.states["zwaveHvacOperationModeID"]]}\', overridden and reset to \'{HVAC_TRANSLATION[zwaveHvacOperationModeID]}\''

                            # if self.globals['trvc'][trvCtlrDevId]['trvSupportsManualSetpoint']:
                            #     if (float(origDev.heatSetpoint) != float(newDev.heatSetpoint)):
                            #         updateRequested = True
                            #         if self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_TRV_HARDWARE:
                            #             updateList[UPDATE_TRV_HEAT_SETPOINT_FROM_DEVICE] = newDev.heatSetpoint
                            #             updateLogItems[UPDATE_TRV_HEAT_SETPOINT_FROM_DEVICE] = f'TRV Heat Setpoint changed on device from {origDev.heatSetpoint} to {newDev.heatSetpoint} [Internal store = {self.globals["trvc"][trvCtlrDevId]["setpointHeatTrv"]}]'
                            #         else:
                            #             updateList[UPDATE_TRV_HEAT_SETPOINT] = newDev.heatSetpoint
                            #             updateLogItems[UPDATE_TRV_HEAT_SETPOINT] = f'TRV Heat Setpoint changed from {origDev.heatSetpoint} to {newDev.heatSetpoint} [Internal store = {self.globals["trvc"][trvCtlrDevId]["setpointHeatTrv"]}]'

                            # if self.globals['trvc'][trvCtlrDevId]['trvSupportsManualSetpoint']:
                            if float(origDev.heatSetpoint) != float(newDev.heatSetpoint):
                                updateRequested = True
                                if self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_TRV_HARDWARE:
                                    updateList[UPDATE_TRV_HEAT_SETPOINT_FROM_DEVICE] = newDev.heatSetpoint
                                    updateLogItems[UPDATE_TRV_HEAT_SETPOINT_FROM_DEVICE] = (
                                        f'TRV Heat Setpoint changed on device from {origDev.heatSetpoint} to {newDev.heatSetpoint} [Internal store was = {self.globals["trvc"][trvCtlrDevId]["setpointHeatTrv"]}]')
                                else:
                                    updateList[UPDATE_TRV_HEAT_SETPOINT] = newDev.heatSetpoint
                                    updateLogItems[UPDATE_TRV_HEAT_SETPOINT] = (
                                        f'TRV Heat Setpoint changed from {origDev.heatSetpoint} to {newDev.heatSetpoint} [Internal store was = {self.globals["trvc"][trvCtlrDevId]["setpointHeatTrv"]}]')

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
                                    updateLogItems[UPDATE_ZWAVE_HVAC_OPERATION_MODE_ID] = (
                                        f'Valve Percentage Open updated from \'{origDev.brightness}\' to \'{newDev.brightness}\' [Internal store was = {self.globals["trvc"][trvCtlrDevId]["valvePercentageOpen"]}]')
                                    if self.globals['trvc'][trvCtlrDevId]['updateCsvFile']:
                                        if self.globals['trvc'][trvCtlrDevId]['updateAllCsvFiles']:
                                            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES, trvCtlrDevId, None])
                                        else:
                                            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['valvePercentageOpen', int(newDev.brightness)]])

                    elif self.globals['devicesToTrvControllerTable'][newDev.id]['type'] == REMOTE:

                        race_condition_result = check_for_race_condition("remote", "REMOTE", "Remote Thermostat device managed by TRV Controller")
                        if race_condition_result:
                            return  # Note that the 'finally:' statement at the end of this deviceUpdated method will return the correct values to Indigo

                        if 'batteryLevel' in newDev.states:
                            if (origDev.batteryLevel != newDev.batteryLevel) or (self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'] != newDev.batteryLevel):
                                self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'] = newDev.batteryLevel
                                updateRequested = True
                                updateList[UPDATE_REMOTE_BATTERY_LEVEL] = newDev.batteryLevel
                                updateLogItems[UPDATE_REMOTE_BATTERY_LEVEL] = (
                                    f'Remote Battery Level updated from {origDev.batteryLevel} to {newDev.batteryLevel} [Internal store was = \'{self.globals["trvc"][trvCtlrDevId]["batteryLevelRemote"]}\']')

                        if trvControllerDev.states['controllerMode'] != self.globals['trvc'][trvCtlrDevId]['controllerMode']:
                            updateRequested = True
                            updateList[UPDATE_CONTROLLER_MODE] = self.globals['trvc'][trvCtlrDevId]['controllerMode']
                            updateLogItems[UPDATE_CONTROLLER_MODE] = (
                                f'Controller Mode updated from {CONTROLLER_MODE_TRANSLATION[trvControllerDev.states["controllerMode"]]} to {CONTROLLER_MODE_TRANSLATION[self.globals["trvc"][trvCtlrDevId]["controllerMode"]]}')
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
                                                origTemp = 10.0  #
                                                newTemp = 10.0
                                                self.logger.error(f'\'{newDev.name}\' is an unknown Remote Thermostat type - remote support disabled for \'{trvControllerDev.name}\'')
                                                del self.globals['devicesToTrvControllerTable'][self.globals['trvc'][trvCtlrDevId]['remoteDevId']]  # Disable Remote Support
                                                self.globals['trvc'][trvCtlrDevId]['remoteDevId'] = 0

                        if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:

                            # origTemp should already have had the offset applied - just need to add it to newTemp to ensure comparison is valid

                            newTempPlusOffset = newTemp + float(self.globals['trvc'][trvCtlrDevId]['remoteTempOffset'])
                            if origTemp != newTempPlusOffset:
                                updateRequested = True
                                updateList[UPDATE_REMOTE_TEMPERATURE] = newTemp  # Send through the original (non-offset) temperature
                                updateLogItems[UPDATE_REMOTE_TEMPERATURE] = (
                                    f'Temperature updated from {origTemp} to {newTempPlusOffset} [Internal store = \'{self.globals["trvc"][trvCtlrDevId]["temperatureRemote"]}\']')
                                if self.globals['trvc'][trvCtlrDevId]['updateCsvFile']:
                                    if self.globals['trvc'][trvCtlrDevId]['updateAllCsvFiles']:
                                        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES, trvCtlrDevId, None])
                                    else:
                                        self.globals['queues']['trvHandler'].put(
                                            [QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_CSV_FILE, trvCtlrDevId, ['temperatureRemote', newTempPlusOffset]])  # The offset temperature for the CSV file

                            if self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl']:
                                if float(newDev.heatSetpoint) != float(origDev.heatSetpoint):
                                    updateRequested = True
                                    updateList[UPDATE_REMOTE_HEAT_SETPOINT_FROM_DEVICE] = newDev.heatSetpoint
                                    updateLogItems[UPDATE_REMOTE_HEAT_SETPOINT_FROM_DEVICE] = (
                                        f'Remote Heat Setpoint changed from {origDev.heatSetpoint} to {newDev.heatSetpoint} [Internal store was = {self.globals["trvc"][trvCtlrDevId]["setpointHeatRemote"]}]')
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
                                    updateLogItems[UPDATE_ZWAVE_EVENT_RECEIVED_REMOTE] = f'Remote Thermostat Z-Wave event received. Time updated to \'{self.globals["trvc"][trvCtlrDevId]["zwaveEventReceivedDateTimeRemote"]}\'. Received count now totals: {self.globals["trvc"][trvCtlrDevId]["zwaveReceivedCountPreviousRemote"]}'

                                # Check if Z-Wave Event has been sent
                                if self.globals['trvc'][trvCtlrDevId]['zwaveSentCountRemote'] > self.globals['trvc'][trvCtlrDevId]['zwaveSentCountPreviousRemote']:
                                    self.globals['trvc'][trvCtlrDevId]['zwaveSentCountPreviousRemote'] = self.globals['trvc'][trvCtlrDevId]['zwaveSentCountRemote']
                                    updateRequested = True
                                    updateList[UPDATE_ZWAVE_EVENT_SENT_REMOTE] = self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeRemote']
                                    updateLogItems[UPDATE_ZWAVE_EVENT_SENT_REMOTE] = f'Remote Thermostat Z-Wave event sent. Time updated to \'{self.globals["trvc"][trvCtlrDevId]["zwaveEventSentDateTimeRemote"]}\'. Sent count now totals: {self.globals["trvc"][trvCtlrDevId]["zwaveSentCountRemote"]}'
                            else:
                                if newDev.lastSuccessfulComm != self.globals['trvc'][trvCtlrDevId]['lastSuccessfulCommRemote']:
                                    self.globals['trvc'][trvCtlrDevId]['eventReceivedCountRemote'] += 1
                                    updateRequested = True
                                    updateList[UPDATE_EVENT_RECEIVED_REMOTE] = f'{newDev.lastSuccessfulComm}'
                                    updateLogItems[UPDATE_EVENT_RECEIVED_REMOTE] = f'Remote Thermostat event received. Time updated to \'{newDev.lastSuccessfulComm}\'. Received count now totals: {self.globals["trvc"][trvCtlrDevId]["eventReceivedCountRemote"]}'

                            self.globals['trvc'][trvCtlrDevId]['lastSuccessfulCommRemote'] = newDev.lastSuccessfulComm

                    if updateRequested:

                        deviceUpdatedLog = deviceUpdatedLog + f'\n==  List of states to be queued for update by TRVHANDLER:'
                        for itemToUpdate in updateList.items():
                            updateKey = itemToUpdate[0]
                            updateValue = itemToUpdate[1]
                            deviceUpdatedLog = deviceUpdatedLog + f'\n==    > Description = {UPDATE_TRANSLATION[updateKey]}, Value = {updateValue}'

                        if self.globals['devicesToTrvControllerTable'][newDev.id]['type'] == TRV:
                            queuedCommand = CMD_UPDATE_TRV_STATES
                        elif self.globals['devicesToTrvControllerTable'][newDev.id]['type'] == VALVE:
                            queuedCommand = CMD_UPDATE_VALVE_STATES
                        else:
                            # Must be Remote
                            queuedCommand = CMD_UPDATE_REMOTE_STATES
                        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, self.globals['deviceUpdatedSequenceCount'], queuedCommand, trvCtlrDevId, [updateList, ]])

                        deviceUpdatedLog = deviceUpdatedLog + f'\n==  Description of updates that will be performed by TRVHANDLER:'
                        for itemToUpdate in updateLogItems.items():
                            # updateKey = itemToUpdate[0]
                            updateValue = itemToUpdate[1]
                            deviceUpdatedLog = deviceUpdatedLog + f'\n==    > {updateValue}'

                        deviceUpdatedLog = deviceUpdatedLog + u'\n==\n======================================================================================================================================================\n\n'

                    else:

                        deviceUpdatedLog = deviceUpdatedLog + f'\n==\n== No updates to \'{DEVICE_TYPE_TRANSLATION[self.globals["devicesToTrvControllerTable"][newDev.id]["type"]]}\' that are of interest to the plugin'
                        deviceUpdatedLog = deviceUpdatedLog + u'\n==\n======================================================================================================================================================\n\n'
                        # deviceUpdatedLog = ''  # TODO: Looks like this was a bug unless it was to suppress this particular message?

                    if len(deviceUpdatedLog) > 0:
                        self.logger.debug(deviceUpdatedLog)

                    # else:
                    #

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

        finally:

            indigo.PluginBase.deviceUpdated(self, origDev, newDev)

    def getActionConfigUiValues(self, valuesDict, typeId, actionId):
        try:
            self.logger.debug(f'getActionConfigUiValues: typeId [{typeId}], actionId [{actionId}], pluginProps[{valuesDict}]')

            errorDict = indigo.Dict()

            # if typeId == "processUpdateSchedule":
            #     devId = actionId  # TRV Controller Device Id

            if typeId == "processBoost":
                boostMode = int(valuesDict.get('boostMode', BOOST_MODE_NOT_SET))
                if boostMode == BOOST_MODE_NOT_SET:
                    valuesDict['boostMode'] = str(BOOST_MODE_SELECT)
        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement
        finally:
            return valuesDict, errorDict  # noqa [Reference before assignment]

    def getDeviceConfigUiValues(self, pluginProps, typeId, devId):
        try:
            if 'remoteDeltaMax' not in pluginProps:
                pluginProps['remoteDeltaMax'] = pluginProps.get('remoteTRVDeltaMax', '5.0')  # This is a fix to transfer the old name value (remoteTRVDeltaMax) to the new name value (remoteDeltaMax)
            # if not 'trvDeltaMax' in pluginProps:
            #      pluginProps['trvDeltaMax'] = '0.0'
            if 'heatingId' not in pluginProps:
                pluginProps['heatingId'] = '-1'
            if 'heatingVarId' not in pluginProps:
                pluginProps['heatingVarId'] = '-1'
            if 'forceTrvOnOff' in pluginProps and 'enableTrvOnOff' not in pluginProps:
                pluginProps['enableTrvOnOff'] = pluginProps['forceTrvOnOff']
                del pluginProps['forceTrvOnOff']
            if 'overrideSetpointHeatMaximum' not in pluginProps:
                pluginProps['overrideSetpointHeatMaximum'] = False
            if 'overrideSetpointHeatMaximumValue' not in pluginProps:
                pluginProps['overrideSetpointHeatMaximumValue'] = 0.0
            if 'trvDeviceSetpointHeatMaximum' not in pluginProps:
                pluginProps['trvDeviceSetpointHeatMaximum'] = pluginProps['setpointHeatMaximum']
            if 'remoteThermostatControlEnabled' not in pluginProps:   # This is a fix to transfer the old name value (remoteThermostatControlEnabled) to the new name value (remoteThermostatControlEnabled)
                pluginProps['remoteThermostatControlEnabled'] = False
                if 'remoteThermostatControlEnabled' in pluginProps:
                    pluginProps['remoteThermostatControlEnabled'] = pluginProps['remoteThermostatControlEnabled']

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement
        finally:
            return super(Plugin, self).getDeviceConfigUiValues(pluginProps, typeId, devId)

    def getPrefsConfigUiValues(self):
        prefsConfigUiValues = self.pluginPrefs
        if "trvVariableFolderName" not in prefsConfigUiValues:
            prefsConfigUiValues["trvVariableFolderName"] = 'TRV'
        if "disableHeatSourceDeviceListFilter" not in prefsConfigUiValues:
            prefsConfigUiValues["disableHeatSourceDeviceListFilter"] = False
        if "delayQueueSeconds" not in prefsConfigUiValues:
            prefsConfigUiValues["delayQueueSeconds"] = 0

        return prefsConfigUiValues

    def shutdown(self):
        self.logger.debug('Shutdown called')

        self.logger.info('\'TRV Controller\' Plugin shutdown complete')

    def startup(self):
        indigo.devices.subscribeToChanges()

        # Subscribe to incoming raw Z-Wave command bytes
        indigo.zwave.subscribeToIncoming()

        # Subscribe to outgoing raw Z-Wave command bytes
        indigo.zwave.subscribeToOutgoing()

        # Initialise dictionary to store internal details about the Z-wave Interpreter
        self.globals[ZWI] = dict()
        self.globals[ZWI][ZWI_INSTANCE] = ZwaveInterpreter(self.exception_handler, self.logger, indigo.devices)  # Instantiate and initialise Z-Wave Interpreter Object for this device

        # TODO: remove this - 18-March-2022
        # ZwaveInterpreter(self.exception_handler, self.logger, indigo.devices)  # noqa [Defined outside __init__] Instantiate and initialise Z-Wave Interpreter Object

        # Create trvHandler process queue
        self.globals['queues']['trvHandler'] = queue.PriorityQueue()  # Used to queue trvHandler commands
        self.globals['queues']['delayHandler'] = queue.Queue()
        self.globals['queues']['initialised'] = True

        self.globals['threads']['trvHandler']['event'] = threading.Event()
        self.globals['threads']['trvHandler']['thread'] = ThreadTrvHandler(self.globals, self.globals['threads']['trvHandler']['event'])
        # self.globals['threads']['trvHandler']['thread'].daemon = True
        self.globals['threads']['trvHandler']['thread'].start()

        self.globals['threads']['delayHandler']['event'] = threading.Event()
        self.globals['threads']['delayHandler']['thread'] = ThreadDelayHandler(self.globals, self.globals['threads']['delayHandler']['event'])
        # self.globals['threads']['delayHandler']['thread'].daemon = True
        self.globals['threads']['delayHandler']['thread'].start()

        try:
            secondsUntilSchedulesRestated = calculateSecondsUntilSchedulesRestated()
            self.globals['timers']['reStateSchedules'] = threading.Timer(float(secondsUntilSchedulesRestated), self.restateSchedulesTriggered, [secondsUntilSchedulesRestated])
            self.globals['timers']['reStateSchedules'].daemon = True
            self.globals['timers']['reStateSchedules'].start()

            self.logger.info(f'TRV Controller has calculated the number of seconds until Schedules restated as {secondsUntilSchedulesRestated}')

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

        self.logger.info('\'TRV Controller\' initialization complete')

    def stopConcurrentThread(self):
        self.logger.debug('Thread shutdown called')

        self.stopThread = True  # noqa - Intsnace attribute stopThread defined outside __init__

    def validateActionConfigUi(self, valuesDict, typeId, actionId):
        try:
            self.logger.debug(f'Validate Action Config UI: typeId = \'{typeId}\', actionId = \'{actionId}\', ValuesDict =\n{valuesDict}\n')

            if typeId == "processUpdateSchedule":

                valuesDict['setpointHeatMinimum'] = float(self.globals['trvc'][actionId]['setpointHeatMinimum'])
                valuesDict['setpointHeatMaximum'] = float(self.globals['trvc'][actionId]['setpointHeatMaximum'])

                # Suppress PyCharm warnings
                # schedule1TimeOn = None
                schedule1TimeOff = None
                # schedule1SetpointHeat = None
                schedule2TimeOn = None
                schedule2TimeOff = None
                # schedule2SetpointHeat = None
                schedule3TimeOn = None
                schedule3TimeOff = None
                # schedule3SetpointHeat = None
                schedule4TimeOn = None
                # schedule4TimeOff = None
                # schedule4SetpointHeat = None

                # Validate Schedule 1
                schedule1Enabled = bool(valuesDict.get('schedule1Enabled', False))
                if schedule1Enabled:
                    scheduleValid, scheduleData = self.validateSchedule(actionId, valuesDict, '1')
                    if not scheduleValid:
                        return False, valuesDict, scheduleData  # i.e. False, valuesDict, errorDict
                    # schedule1TimeOn = scheduleData[0]
                    schedule1TimeOff = scheduleData[1]
                    # schedule1SetpointHeat = scheduleData[2]

                # Validate Schedule 2
                schedule2Enabled = bool(valuesDict.get('schedule2Enabled', False))
                if schedule2Enabled:
                    scheduleValid, scheduleData = self.validateSchedule(actionId, valuesDict, '2')
                    if not scheduleValid:
                        return False, valuesDict, scheduleData  # i.e. False, valuesDict, errorDict
                    schedule2TimeOn = scheduleData[0]
                    schedule2TimeOff = scheduleData[1]
                    # schedule2SetpointHeat = scheduleData[2]

                # Validate Schedule 3
                schedule3Enabled = bool(valuesDict.get('schedule3Enabled', False))
                if schedule3Enabled:
                    scheduleValid, scheduleData = self.validateSchedule(actionId, valuesDict, '3')
                    if not scheduleValid:
                        return False, valuesDict, scheduleData  # i.e. False, valuesDict, errorDict
                    schedule3TimeOn = scheduleData[0]
                    schedule3TimeOff = scheduleData[1]
                    # schedule3SetpointHeat = scheduleData[2]

                # Validate Schedule 4
                schedule4Enabled = bool(valuesDict.get('schedule4Enabled', False))
                if schedule4Enabled:
                    scheduleValid, scheduleData = self.validateSchedule(actionId, valuesDict, '4')
                    if not scheduleValid:
                        return False, valuesDict, scheduleData  # i.e. False, valuesDict, errorDict
                    schedule4TimeOn = scheduleData[0]
                    # schedule4TimeOff = scheduleData[1]
                    # schedule4SetpointHeat = scheduleData[2]

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
                            errorDict[
                                'showAlertText'] = f'The Schedule One OFF time [{schedule1TimeOff}] must be before the Schedule Two ON time [{schedule2TimeOn}] and there must be at least 10 minutes between the Schedule One OFF time and Schedule Two ON time.'
                            return False, valuesDict, errorDict
                    if schedule3Enabled:
                        if schedule1TimeOff < schedule3TimeOn:
                            secondsDelta = secondsFromHHMM(schedule3TimeOn) - secondsFromHHMM(schedule1TimeOff)
                        else:
                            secondsDelta = 0
                        if secondsDelta < 600:  # 10 minutes (600 seconds) check
                            errorDict = indigo.Dict()
                            errorDict['schedule1TimeOff'] = 'The Schedule One heating OFF time must end before the Schedule Three heating ON time'
                            errorDict['schedule3TimeOn'] = 'The Schedule Three heating On time must start after the Schedule One heating Off time'
                            errorDict[
                                'showAlertText'] = f'The Schedule One OFF time [{schedule1TimeOff}] must be before the Schedule Three ON time [{schedule3TimeOn}] and there must be at least 10 minutes between the Schedule One OFF time and Schedule Three ON time.'
                            return False, valuesDict, errorDict
                    if schedule4Enabled:
                        if schedule1TimeOff < schedule4TimeOn:
                            secondsDelta = secondsFromHHMM(schedule4TimeOn) - secondsFromHHMM(schedule1TimeOff)
                        else:
                            secondsDelta = 0
                        if secondsDelta < 600:  # 10 minutes (600 seconds) check
                            errorDict = indigo.Dict()
                            errorDict['schedule1TimeOff'] = 'The Schedule One heating OFF time must end before the Schedule Four heating ON time'
                            errorDict['schedule4TimeOn'] = 'The Schedule Four heating On time must start after the Schedule One heating Off time'
                            errorDict[
                                'showAlertText'] = f'The Schedule One OFF time [{schedule1TimeOff}] must be before the Schedule Four ON time [{schedule4TimeOn}] and there must be at least 10 minutes between the Schedule One OFF time and Schedule Four ON time.'
                            return False, valuesDict, errorDict

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
                            errorDict[
                                'showAlertText'] = f'The Schedule Two OFF time [{schedule2TimeOff}] must be before the Schedule Three ON time [{schedule3TimeOn}] and there must be at least 10 minutes between the Schedule Two OFF time and Schedule Three ON time.'
                            return False, valuesDict, errorDict

                    if schedule4Enabled:
                        if schedule2TimeOff < schedule4TimeOn:
                            secondsDelta = secondsFromHHMM(schedule4TimeOn) - secondsFromHHMM(schedule2TimeOff)
                        else:
                            secondsDelta = 0
                        if secondsDelta < 600:  # 10 minutes (600 seconds) check
                            errorDict = indigo.Dict()
                            errorDict['schedule2TimeOff'] = 'The Schedule Two heating OFF time must end before the Schedule Four heating ON time'
                            errorDict['schedule4TimeOn'] = 'The Schedule Four heating On time must start after the Schedule Two heating Off time'
                            errorDict[
                                'showAlertText'] = f'The Schedule Two OFF time [{schedule2TimeOff}] must be before the Schedule Four ON time [{schedule4TimeOn}] and there must be at least 10 minutes between the Schedule Two OFF time and Schedule Four ON time.'
                            return False, valuesDict, errorDict

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
                            errorDict[
                                'showAlertText'] = f'The Schedule Three OFF time [{schedule2TimeOff}] must be before the Schedule Four ON time [{schedule4TimeOn}] and there must be at least 10 minutes between the Schedule Three OFF time and Schedule Four ON time.'
                            return False, valuesDict, errorDict

            elif typeId == "processBoost":

                boostMode = int(valuesDict.get('boostMode', 0))
                if boostMode == BOOST_MODE_SELECT:
                    errorDict = indigo.Dict()
                    errorDict['boostMode'] = 'You must select a boost mode: \'Delta T\' or \'Setpoint\'.'
                    errorDict['showAlertText'] = 'You must select a boost mode: \'Delta T\' or \'Setpoint\'.'
                    return False, valuesDict, errorDict

                if boostMode == BOOST_MODE_DELTA_T:  # Validate deltaT
                    valid = False
                    try:
                        boostDeltaT = float(valuesDict.get('boostDeltaT', 3))
                        valid = True
                    except ValueError:
                        boostDeltaT = 3  # To suppress PyCharm warning

                    if not valid or boostDeltaT < 1 or boostDeltaT > 5 or boostDeltaT % 0.5 != 0:
                        errorDict = indigo.Dict()
                        errorDict['boostDeltaT'] = 'Boost Delta T must be a numeric value between 1 and 5 (inclusive) e.g 2.5'
                        errorDict['showAlertText'] = 'You must enter a valid Delta T to boost the temperature by. It must be set between 1 and 5 (inclusive) and a multiple of 0.5.'
                        return False, valuesDict, errorDict

                else:  # Validate Setpoint

                    valid = False
                    try:
                        boostSetpoint = float(valuesDict.get('boostSetpoint', 3.0))
                        valid = True
                    except ValueError:
                        boostSetpoint = 3.0  # To suppress PyCharm warning

                    if actionId in self.globals['trvc']:
                        setpointHeatMinimum = float(self.globals['trvc'][actionId]['setpointHeatMinimum'])
                        setpointHeatMaximum = float(self.globals['trvc'][actionId]['setpointHeatMaximum'])
                    else:
                        errorDict = indigo.Dict()
                        errorDict['boostSetpoint'] = 'Unable to test Setpoint temperature against allowed minimum/maximum.'
                        errorDict['showAlertText'] = f'Unable to test Setpoint temperature against allowed minimum/maximum - make sure device \'{indigo.devices[actionId].name}\' is enabled.'
                        return False, valuesDict, errorDict

                    if not valid or boostSetpoint < setpointHeatMinimum or boostSetpoint > setpointHeatMaximum or boostSetpoint % 0.5 != 0:
                        errorDict = indigo.Dict()
                        errorDict['boostSetpoint'] = f'Setpoint temperature must be numeric and set between {setpointHeatMinimum} and {setpointHeatMaximum} (inclusive)'
                        errorDict['showAlertText'] = f'You must enter a valid Setpoint temperature for the TRV. It must be numeric and set between {setpointHeatMinimum} and {setpointHeatMaximum} (inclusive) and a multiple of 0.5.'
                        return False, valuesDict, errorDict

                valid = False
                try:
                    boostMinutes = int(valuesDict.get('boostMinutes', 20))
                    valid = True
                except ValueError:
                    boostMinutes = 20  # To suppress PyCharm warning

                if not valid or boostMinutes < 5 or boostMinutes > 120:
                    errorDict = indigo.Dict()
                    errorDict['boostMinutes'] = 'Boost Minutes must be an integer and set between 5 and 120 (inclusive) e.g 20'
                    errorDict['showAlertText'] = 'You must enter a valid number of minutes to boost the temperature by. It must be a numeric value and set between 5 and 120 (inclusive).'
                    return False, valuesDict, errorDict

            elif typeId == "processExtend":

                # Validate extend increment minutes
                valid = False
                try:
                    extendIncrementMinutes = int(valuesDict.get('extendIncrementMinutes', 15))
                    valid = True
                except ValueError:
                    extendIncrementMinutes = 15  # To suppress PyCharm warning
                if not valid or extendIncrementMinutes < 15 or extendIncrementMinutes > 60:
                    errorDict = indigo.Dict()
                    errorDict["extendIncrementMinutes"] = "The Extend Increment Minutes must be an integer and set between 15 and 60 (inclusive)"
                    errorDict[
                        'showAlertText'] = "You must enter a valid Extend Increment Minutes (length of time to increase extend by) for the TRV. It must be an integer and set between 15 and 60 (inclusive)."
                    return False, valuesDict, errorDict

                # Validate extend maximum minutes
                valid = False
                try:
                    extendMaximumMinutes = int(valuesDict.get('extendMaximumMinutes', 15))
                    valid = True
                except ValueError:
                    extendMaximumMinutes = 15  # To suppress PyCharm warning

                if not valid or extendMaximumMinutes < 15 or extendMaximumMinutes > 1080:
                    errorDict = indigo.Dict()
                    errorDict["extendMaximumMinutes"] = "The Extend Maximum Minutes must be an integer and set between 15 and 1080 (18 hours!) (inclusive)"
                    errorDict[
                        'showAlertText'] = "You must enter a valid Extend Maximum Minutes (maximum length of time to extend by) for the TRV. It must be an integer and set between 15 and 1080 (18 hours!) (inclusive)."
                    return False, valuesDict, errorDict

            elif typeId == "processUpdateAllCsvFilesViaPostgreSQL":

                # Validate Override Default Retention Hours
                valid = False
                overrideDefaultRetentionHours = ''
                try:
                    overrideDefaultRetentionHours = valuesDict.get('overrideDefaultRetentionHours', '')
                    if overrideDefaultRetentionHours == '':
                        overrideDefaultRetentionHours = 1024  # A random large number for validity check
                        valid = True
                    else:
                        overrideDefaultRetentionHours = int(valuesDict.get('overrideDefaultRetentionHours', ''))
                        valid = True
                except ValueError:
                    pass

                if not valid or overrideDefaultRetentionHours < 1:
                    errorDict = indigo.Dict()
                    errorDict["overrideDefaultRetentionHours"] = "The Override Default Retention Hours must be blank or an integer greater than 0"
                    errorDict[
                        'showAlertText'] = "You must leave the Override Default Retention Hours blank or enter a valid Retention Hours to retain the CSV data. If set it must be an integer and greater than zero."
                    return False, valuesDict, errorDict

            return True, valuesDict

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def validateDeviceConfigUi(self, valuesDict, typeId, devId):  # Validate TRV Thermostat Controller

        try:
            # Validate TRV Device
            trvDevId = 0
            valid = False
            try:
                trvDevId = int(valuesDict.get('trvDevId', 0))
                if trvDevId != 0 and valuesDict['supportedModel'] != 'Unknown TRV Model':
                    valid = True
            except Exception:
                pass
            if not valid:
                try:
                    model = f'a \'{indigo.devices[trvDevId].model}\' is not a TRV known by the plugin.'
                except KeyError:
                    model = 'no device selected!'
                errorDict = indigo.Dict()
                errorDict['trvDevId'] = 'Select a known TRV device'
                errorDict['showAlertText'] = f'You must select a TRV device  to monitor which is known by the plugin; {model}'
                return False, valuesDict, errorDict

            self.trvThermostatDeviceSelected(valuesDict, typeId, devId)

            overrideSetpointHeatMaximum = bool(valuesDict.get('overrideSetpointHeatMaximum', False))
            if overrideSetpointHeatMaximum:
                overrideSetpointHeatMaximumValue = int(valuesDict.get('overrideSetpointHeatMaximumValue', 0))
                valuesDictTrvDeviceSetpointHeatMaximum = float(valuesDict['trvDeviceSetpointHeatMaximum'])
                if 21.0 < overrideSetpointHeatMaximumValue < valuesDictTrvDeviceSetpointHeatMaximum:
                    valuesDict['setpointHeatMaximum'] = overrideSetpointHeatMaximumValue
                else:
                    errorDict = indigo.Dict()
                    errorDict['overrideSetpointHeatMaximumValue'] = 'Override Setpoint Maximum Value is invalid'
                    errorDict['showAlertText'] = 'Override Setpoint Maximum Value must be > 21 and less than TRV Maximum Settable Temperature [FULLY ON] value.'
                    return False, valuesDict, errorDict

            # # Validate TRV Delta Maximum
            # trvDeltaMax = float(valuesDict.get('trvDeltaMax', 0.0))
            # if trvDeltaMax < 0.0 or trvDeltaMax > 10.0 or trvDeltaMax % 0.5 != 0:
            #     errorDict = indigo.Dict()
            #     errorDict['trvDeltaMax'] = 'TRV Delta Max must be set between 0.0 and 10.0 (inclusive)'
            #     errorDict['showAlertText'] = 'You must enter a valid maximum number of degrees to exceed the TRV Heat Setpoint by. It must be set between 0.0 and 10.0 (inclusive) and a multiple of 0.5.'
            #     return False, valuesDict, errorDict

            # Validate Device Heat Source Controller
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
                            # else:
                            #     heatingId = 0
            except Exception:
                pass
            if not valid:
                errorDict = indigo.Dict()
                errorDict['heatingId'] = 'Select a Heat Source Controller device or Not Required'
                errorDict['showAlertText'] = 'You must select a Heat Source Controller to switch on heat for the TRV or specify Not Required.'
                return False, valuesDict, errorDict

            # Validate Variable Heat Source Controller
            valid = False
            try:
                heatingVarId  = int(valuesDict.get('heatingVarId', -1))
                if heatingVarId != -1:
                    valid = True
            except Exception:
                pass
            if not valid:
                errorDict = indigo.Dict()
                errorDict['heatingVarId'] = 'Select a Heat Source Controller variable or Not Required'
                errorDict['showAlertText'] = 'You must select a Heat Source Controller to switch on heat for the TRV or specify Not Required.'
                return False, valuesDict, errorDict

            # Check whether to validate Remote Thermostat
            remoteDevId = 0
            valid = False
            remoteThermostatControlEnabled = bool(valuesDict.get('remoteThermostatControlEnabled', False))
            if remoteThermostatControlEnabled:
                remoteDevId = int(valuesDict.get('remoteDevId', 0))
                if remoteDevId != 0 and indigo.devices[remoteDevId].deviceTypeId != u'trvController':
                    remoteDev = indigo.devices[remoteDevId]
                    # Deprecated 'subModel' code follows ...
                    # if (remoteDev.subModel == u'Temperature'
                    #         or remoteDev.subModel == u'Temperature 1'
                    #         or remoteDev.subModel == u'Thermostat'
                    #         or remoteDev.subModel[0:7].lower() == u'sensor '
                    # if (remoteDev.subType == u'Temperature'
                    #         or remoteDev.subType == u'Thermostat'
                    #         or remoteDev.subType == u'Sensor'
                    #         or u'temperatureInput1' in remoteDev.states
                    #         or u'temperature' in remoteDev.states
                    #         or u'Temperature' in remoteDev.states):
                    #     valid = True

                    if type(remoteDev) == indigo.ThermostatDevice or type(remoteDev) == indigo.SensorDevice:
                        num_temperature_inputs = int(remoteDev.ownerProps.get("NumTemperatureInputs", "0"))
                        if num_temperature_inputs > 0:
                            valid = True
                else:
                    remoteDevId = 0
                if not valid:
                    try:
                        model = f'a \'{indigo.devices[remoteDevId].model}\' is not a Remote Thermostat understood by this plugin.'
                    except KeyError:
                        model = u'no device selected!'
                    errorDict = indigo.Dict()
                    errorDict[u'remoteDevId'] = u'Select a Remote Thermostat device'
                    errorDict[u'showAlertText'] = f'You must select a Remote thermostat to control the TRV; {model}'
                    return False, valuesDict, errorDict

                if remoteDevId != 0:

                    # Validate Remote Delta Maximum
                    valid = False
                    try:
                        remoteDeltaMax = float(valuesDict.get('remoteDeltaMax', 5.0))
                        valid = True
                    except ValueError:
                        remoteDeltaMax = 5.0  # Set to avoid pyCharm warning message on next line
                    if not valid or remoteDeltaMax < 0.0 or remoteDeltaMax > 10.0 or remoteDeltaMax % 0.5 != 0:
                        errorDict = indigo.Dict()
                        errorDict['remoteDeltaMax'] = 'Remote Delta Max must be set between 0.0 and 10.0 (inclusive)'
                        errorDict['showAlertText'] = 'You must enter a valid maximum number of degrees to exceed the TRV Heat Setpoint for the remote thermostat. It must be set between 0.0 and 10.0 (inclusive) and a multiple of 0.5.'
                        return False, valuesDict, errorDict

                    # Validate Remote Temperature Offset
                    valid = False
                    try:
                        remoteTempOffset = float(valuesDict.get('remoteTempOffset', 0.0))
                        valid = True
                    except ValueError:
                        remoteTempOffset = 0.0  # Set to avoid pyCharm warning message on next line
                    if not valid or remoteTempOffset < -5.0 or remoteDeltaMax > 5.0:
                        errorDict = indigo.Dict()
                        errorDict['remoteTempOffset'] = 'Remote Temperature Offset must be set between -5.0 and 5.0 (inclusive)'
                        errorDict['showAlertText'] = 'You must enter a valid Remote Temperature Offset. It must be set between -5.0 and 5.0 (inclusive).'
                        return False, valuesDict, errorDict

            # Validate CSV Fields

            csvCreationMethod = int(valuesDict.get('csvCreationMethod', 0))

            if csvCreationMethod == '1' or csvCreationMethod == '2':
                csvShortName = valuesDict.get('csvShortName', '')
                if len(csvShortName) < 1 or len(csvShortName) > 10:
                    errorDict = indigo.Dict()
                    errorDict['csvShortName'] = 'Short Name must be present and have a length between 1 and 10 (inclusive).'
                    errorDict['showAlertText'] = 'Short Name must be present and have a length between 1 and 10 (inclusive).'
                    return False, valuesDict, errorDict
                valid = False
                try:
                    csvRetentionPeriodHours = int(valuesDict.get('csvRetentionPeriodHours', 24))
                    if csvRetentionPeriodHours > 0:
                        valid = True
                except ValueError:
                    pass
                if not valid:
                    errorDict = indigo.Dict()
                    errorDict['csvRetentionPeriodHours'] = 'Retention Period (Hours) must be a positive integer.'
                    errorDict['showAlertText'] = 'Retention Period (Hours) must be a positive integer.'
                    return False, valuesDict, errorDict

            # Validate Polling Fields

            supportsWakeup = valuesDict.get('supportsWakeup', 'true')
            if supportsWakeup == 'false':
                valid = False
                try:
                    pollingScheduleActive = int(valuesDict.get('pollingScheduleActive', 5))
                    if pollingScheduleActive >= 0:
                        valid = True
                except ValueError:
                    pass
                if not valid:
                    errorDict = indigo.Dict()
                    errorDict['pollingScheduleActive'] = 'Polling Minutes [Schedule Active] must be a positive integer or zero to disable.'
                    errorDict['showAlertText'] = 'Polling Minutes [Schedule Active] must be a positive integer or zero to disable.'
                    return False, valuesDict, errorDict

                valid = False
                try:
                    pollingScheduleInactive = int(valuesDict.get('pollingScheduleInactive', 5))
                    if pollingScheduleInactive >= 0:
                        valid = True
                except ValueError:
                    pass
                if not valid:
                    errorDict = indigo.Dict()
                    errorDict['pollingScheduleInactive'] = 'Polling Minutes [Schedule Inactive] must be a positive integer or zero to disable.'
                    errorDict['showAlertText'] = 'Polling Minutes [Schedule Inactive] must be a positive integer or zero to disable.'
                    return False, valuesDict, errorDict

                valid = False
                try:
                    pollingSchedulesNotEnabled = int(valuesDict.get('pollingSchedulesNotEnabled', 5))
                    if pollingSchedulesNotEnabled >= 0:
                        valid = True
                except ValueError:
                    pass
                if not valid:
                    errorDict = indigo.Dict()
                    errorDict['pollingSchedulesNotEnabled'] = 'Polling Minutes [Schedules Not Enabled] must be a positive integer or zero to disable.'
                    errorDict['showAlertText'] = 'Polling Minutes [Schedules Not Enabled] must be a positive integer or zero to disable.'
                    return False, valuesDict, errorDict

                valid = False
                try:
                    pollingBoostEnabled = int(valuesDict.get('pollingBoostEnabled', 5))
                    if pollingBoostEnabled >= 0:
                        valid = True
                except ValueError:
                    pass
                if not valid:
                    errorDict = indigo.Dict()
                    errorDict['pollingBoostEnabled'] = 'Polling Minutes [Boost Enabled] must be a positive integer or zero to disable.'
                    errorDict['showAlertText'] = 'Polling Minutes [Boost Enabled] must be a positive integer or zero to disable.'
                    return False, valuesDict, errorDict

            # Validate Device Start Method fields
            setpointHeatDeviceStartMethod = int(valuesDict.get('setpointHeatDeviceStartMethod', DEVICE_START_SETPOINT_DEVICE_MINIMUM))
            if setpointHeatDeviceStartMethod == DEVICE_START_SETPOINT_SPECIFIED:
                valid = False
                try:
                    setpointHeatDeviceStartDefault = float(valuesDict.get('setpointHeatDeviceStartDefault', 8.0))
                    if (8 <= setpointHeatDeviceStartDefault <= 30) and setpointHeatDeviceStartDefault % 0.5 == 0.0:
                        valid = True
                except Exception:
                    pass
                if not valid:
                    errorDict = indigo.Dict()
                    errorDict['setpointHeatDeviceStartDefault'] = 'Temperature must be set between 8 and 30 (inclusive)'
                    errorDict['showAlertText'] = 'You must enter a valid \'Device Start\' temperature for the TRV. It must be set between 8 and 30 (inclusive) and a multiple of 0.5.'
                    return False, valuesDict, errorDict

            # Validate default ON temperature
            valid = False
            try:
                setpointHeatOnDefault = float(valuesDict.get('setpointHeatOnDefault', 0))
                if (10.0 <= setpointHeatOnDefault <= 30.0) and setpointHeatOnDefault % 0.5 == 0.0:
                    valid = True
            except Exception:
                pass
            if not valid:
                errorDict = indigo.Dict()
                errorDict['setpointHeatOnDefault'] = 'Temperature must be set between 10 and 30 (inclusive)'
                errorDict['showAlertText'] = 'You must enter a valid Turn On temperature for the TRV. It must be set between 10 and 30 (inclusive) and a multiple of 0.5.'
                return False, valuesDict, errorDict

            # Suppress PyCharm warnings
            # schedule1TimeOn = None
            schedule1TimeOff = None
            # schedule1SetpointHeat = None
            schedule2TimeOn = None
            schedule2TimeOff = None
            # schedule2SetpointHeat = None
            schedule3TimeOn = None
            schedule3TimeOff = None
            # schedule3SetpointHeat = None
            schedule4TimeOn = None
            # schedule4TimeOff = None
            # schedule4SetpointHeat = None

            # Validate Schedule 1
            schedule1Enabled = bool(valuesDict.get('schedule1Enabled', False))
            if schedule1Enabled:
                scheduleValid, scheduleData = self.validateSchedule(devId, valuesDict, '1')
                if not scheduleValid:
                    return False, valuesDict, scheduleData  # i.e. False, valuesDict, errorDict
                # schedule1TimeOn = scheduleData[0]
                schedule1TimeOff = scheduleData[1]
                # schedule1SetpointHeat = scheduleData[2]

            # Validate Schedule 2
            schedule2Enabled = bool(valuesDict.get('schedule2Enabled', False))
            if schedule2Enabled:
                scheduleValid, scheduleData = self.validateSchedule(devId, valuesDict, '2')
                if not scheduleValid:
                    return False, valuesDict, scheduleData  # i.e. False, valuesDict, errorDict
                schedule2TimeOn = scheduleData[0]
                schedule2TimeOff = scheduleData[1]
                # schedule2SetpointHeat = scheduleData[2]

            # Validate Schedule 3
            schedule3Enabled = bool(valuesDict.get('schedule3Enabled', False))
            if schedule3Enabled:
                scheduleValid, scheduleData = self.validateSchedule(devId, valuesDict, '3')
                if not scheduleValid:
                    return False, valuesDict, scheduleData  # i.e. False, valuesDict, errorDict
                schedule3TimeOn = scheduleData[0]
                schedule3TimeOff = scheduleData[1]
                # schedule3SetpointHeat = scheduleData[2]

            # Validate Schedule 3
            schedule4Enabled = bool(valuesDict.get('schedule4Enabled', False))
            if schedule4Enabled:
                scheduleValid, scheduleData = self.validateSchedule(devId, valuesDict, '4')
                if not scheduleValid:
                    return False, valuesDict, scheduleData  # i.e. False, valuesDict, errorDict
                schedule4TimeOn = scheduleData[0]
                # schedule4TimeOff = scheduleData[1]
                # schedule4SetpointHeat = scheduleData[2]

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
                        errorDict['showAlertText'] = f'The Schedule One OFF time [{schedule1TimeOff}] must be before the Schedule Two ON time [{schedule2TimeOn}] and there must be at least 10 minutes between the Schedule One OFF time and Schedule Two ON time.'
                        return False, valuesDict, errorDict
                if schedule3Enabled:
                    if schedule1TimeOff < schedule3TimeOn:
                        secondsDelta = secondsFromHHMM(schedule3TimeOn) - secondsFromHHMM(schedule1TimeOff)
                    else:
                        secondsDelta = 0
                    if secondsDelta < 600:  # 10 minutes (600 seconds) check
                        errorDict = indigo.Dict()
                        errorDict['schedule1TimeOff'] = 'The Schedule One heating OFF time must end before the Schedule Three heating ON time'
                        errorDict['schedule3TimeOn'] = 'The Schedule Three heating On time must start after the Schedule One heating Off time'
                        errorDict['showAlertText'] = f'The Schedule One OFF time [{schedule1TimeOff}] must be before the Schedule Three ON time [{schedule3TimeOn}] and there must be at least 10 minutes between the Schedule One OFF time and Schedule Three ON time.'
                        return False, valuesDict, errorDict
                if schedule4Enabled:
                    if schedule1TimeOff < schedule4TimeOn:
                        secondsDelta = secondsFromHHMM(schedule4TimeOn) - secondsFromHHMM(schedule1TimeOff)
                    else:
                        secondsDelta = 0
                    if secondsDelta < 600:  # 10 minutes (600 seconds) check
                        errorDict = indigo.Dict()
                        errorDict['schedule1TimeOff'] = 'The Schedule One heating OFF time must end before the Schedule Four heating ON time'
                        errorDict['schedule4TimeOn'] = 'The Schedule Four heating On time must start after the Schedule One heating Off time'
                        errorDict['showAlertText'] = f'The Schedule One OFF time [{schedule1TimeOff}] must be before the Schedule Four ON time [{schedule3TimeOn}] and there must be at least 10 minutes between the Schedule One OFF time and Schedule Four ON time.'
                        return False, valuesDict, errorDict
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
                        errorDict['showAlertText'] = f'The Schedule Two OFF time [{schedule2TimeOff}] must be before the Schedule Three ON time [{schedule3TimeOn}] and there must be at least 10 minutes between the Schedule Two OFF time and Schedule Three ON time.'
                        return False, valuesDict, errorDict
                if schedule4Enabled:
                    if schedule2TimeOff < schedule4TimeOn:
                        secondsDelta = secondsFromHHMM(schedule4TimeOn) - secondsFromHHMM(schedule2TimeOff)
                    else:
                        secondsDelta = 0
                    if secondsDelta < 600:  # 10 minutes (600 seconds) check
                        errorDict = indigo.Dict()
                        errorDict['schedule2TimeOff'] = 'The Schedule Two heating OFF time must end before the Schedule Four heating ON time'
                        errorDict['schedule4TimeOn'] = 'The Schedule Four heating On time must start after the Schedule Two heating Off time'
                        errorDict['showAlertText'] = f'The Schedule Two OFF time [{schedule2TimeOff}] must be before the Schedule Four ON time [{schedule4TimeOn}] and there must be at least 10 minutes between the Schedule Two OFF time and Schedule Four ON time.'
                        return False, valuesDict, errorDict

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
                        errorDict['showAlertText'] = f'The Schedule Three OFF time [{schedule3TimeOff}] must be before the Schedule Four ON time [{schedule4TimeOn}] and there must be at least 10 minutes between the Schedule Three OFF time and Schedule Four ON time.'
                        return False, valuesDict, errorDict

            return True, valuesDict

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def validatePrefsConfigUi(self, values_dict):   # noqa - Method is not declared static

        return True, values_dict

    # noinspection PyUnusedLocal
    def zwaveCommandQueued(self, zwaveCommand):  # Not yet available in Indigo API :)

        self.logger.error('QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED==QUEUED')

    def zwaveCommandReceived(self, zwave_command):
        try:
            zwave_report_prefix = f"{u'':-{u'^'}22}> Z-WAVE "  # 22 dashes as first part of prefix

            now_time = indigo.server.getTime()
            now_time_string = now_time.strftime('%Y-%m-%d %H:%M:%S')

            nodeId = zwave_command['nodeId']  # Can be None!

            zwave_report_additional_detail = u""

            if nodeId and nodeId in self.globals['zwave']['WatchList']:

                # Interpret Z-Wave Command
                zw_interpretation = self.globals[ZWI][ZWI_INSTANCE].interpret_zwave(True, zwave_command)  # True is to indicate Z-Wave Message received

                if zw_interpretation is not None and zw_interpretation[ZW_INTERPRETATION_ATTEMPTED]:
                    # self.zwave_log(zw_interpretation[ZW_INDIGO_DEVICE], zw_interpretation[ZW_INTERPRETATION_OVERVIEW_UI], zw_interpretation[ZW_INTERPRETATION_DETAIL_UI])

                    address = zw_interpretation[ZW_NODE_ID]

                    if address in self.globals['zwave']['addressToDevice']:

                        dev = indigo.devices[self.globals['zwave']['addressToDevice'][address]['devId']]  # TRV or Remote
                        devId = dev.id
                        devType = self.globals['zwave']['addressToDevice'][address]['type']
                        trvcDev = indigo.devices[self.globals['zwave']['addressToDevice'][address]['trvcId']]  # TRV Controller
                        trvCtlrDevId = trvcDev.id
                        if devType == TRV:
                            self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeTrv'] = now_time_string
                            if 'zwaveReceivedCountTrv' in self.globals['trvc'][trvCtlrDevId]:
                                self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountTrv'] += 1
                            else:
                                self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountTrv'] = 1

                            self.globals['trvc'][trvCtlrDevId]['zwaveLastReceivedCommandTrv'] = zw_interpretation[ZW_COMMAND_CLASS]

                            if self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'] > 0:
                                if self.globals['trvc'][trvCtlrDevId]['zwaveWakeupDelayTrv']:
                                    self.globals['trvc'][trvCtlrDevId]['zwaveWakeupDelayTrv'] = False
                                    self.logger.info(
                                        f'Z-Wave connection re-established with {"TRV device"} \'{indigo.devices[devId].name}\', controlled by \'{indigo.devices[trvCtlrDevId].name}\'. This device had previously missed a wakeup.')
                                    trvcDev.updateStateImageOnServer(indigo.kStateImageSel.HvacHeatMode)

                                nextWakeupMissedSeconds = (self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalTrv'] + 2) * 60  # Add 2 minutes to next expected wakeup
                                if devId in self.globals['timers']['zwaveWakeupCheck']:
                                    self.globals['timers']['zwaveWakeupCheck'][devId].cancel()
                                self.globals['timers']['zwaveWakeupCheck'][devId] = threading.Timer(float(nextWakeupMissedSeconds), self.zwaveWakeupMissedTriggered, [trvCtlrDevId, devType, devId])
                                self.globals['timers']['zwaveWakeupCheck'][devId].daemon = True
                                self.globals['timers']['zwaveWakeupCheck'][devId].start()
                                # zwaveReport = zwaveReport + f"\nZZ  TRV Z-WAVE > Next wakeup missed alert in {nextWakeupMissedSeconds} seconds"

                        else:  # Must be Remote
                            self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeRemote'] = now_time_string
                            if 'zwaveReceivedCountRemote' in self.globals['trvc'][trvCtlrDevId]:
                                self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountRemote'] += 1
                            else:
                                self.globals['trvc'][trvCtlrDevId]['zwaveReceivedCountRemote'] = 1
                            self.globals['trvc'][trvCtlrDevId]['zwaveLastReceivedCommandRemote'] = zw_interpretation[ZW_COMMAND_CLASS]

                            if self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalRemote'] > 0:
                                if self.globals['trvc'][trvCtlrDevId]['zwaveWakeupDelayRemote']:
                                    self.globals['trvc'][trvCtlrDevId]['zwaveWakeupDelayRemote'] = False
                                    self.logger.info(
                                        f'Z-Wave connection re-established with {u"Remote Thermostat device"} \'{indigo.devices[devId].name}\', controlled by \'{indigo.devices[trvCtlrDevId].name}\'. This device had previously missed a wakeup.')

                                    trvcDev.updateStateImageOnServer(indigo.kStateImageSel.HvacHeatMode)

                                nextWakeupMissedSeconds = (self.globals['trvc'][trvCtlrDevId]['zwaveWakeupIntervalRemote'] + 2) * 60  # Add 2 minutes to next expected wakeup
                                if devId in self.globals['timers']['zwaveWakeupCheck']:
                                    self.globals['timers']['zwaveWakeupCheck'][devId].cancel()
                                self.globals['timers']['zwaveWakeupCheck'][devId] = threading.Timer(float(nextWakeupMissedSeconds), self.zwaveWakeupMissedTriggered, [trvCtlrDevId, devType, devId])
                                self.globals['timers']['zwaveWakeupCheck'][devId].daemon = True
                                self.globals['timers']['zwaveWakeupCheck'][devId].start()
                                # zwaveReport = zwaveReport + f"\nZZ  TRV Z-WAVE > Next wakeup missed alert in {nextWakeupMissedSeconds} seconds"

                        if zw_interpretation[ZW_COMMAND_CLASS] == ZW_THERMOSTAT_SETPOINT:
                            if devType == TRV and self.globals['trvc'][trvCtlrDevId]['trvSupportsManualSetpoint']:
                                self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_TRV_HARDWARE
                                zwave_report_additional_detail = f', Pending Controller Mode = {CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_TRV_HARDWARE]}'
                            elif devType == REMOTE and self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl']:
                                self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_REMOTE_HARDWARE
                                zwave_report_additional_detail = f', Pending Controller Mode = {CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_REMOTE_HARDWARE]}'

                        elif zw_interpretation[ZW_COMMAND_CLASS] == ZW_SWITCH_MULTILEVEL:
                            zwaveCommandValve = zw_interpretation[ZW_VALUE]
                            if zwaveCommandValve == 0:
                                zwave_report_additional_detail = u', Valve = Closed'
                            else:
                                zwave_report_additional_detail = f', Valve = Open {zwaveCommandValve}%'

                        elif zw_interpretation[ZW_COMMAND_CLASS] == ZW_THERMOSTAT_MODE:
                            pass
                            # zwave_report_additional_detail = f', Mode = {zw_interpretation[ZW_MODE_UI]}'

                            # if devType == TRV:
                            #     self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_TRV_HARDWARE
                            # else:  # Must be Remote as can't be a valve
                            #     self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_REMOTE_HARDWARE

                        elif zw_interpretation[ZW_COMMAND_CLASS] == ZW_SENSOR_MULTILEVEL:
                            pass
                            # zwave_report_additional_detail = f', Temperature = {zw_interpretation[ZW_VALUE_UI]}{zw_interpretation[ZW_SCALE_UI_COMPACT]}'

                        if zw_interpretation[ZW_COMMAND_CLASS] == ZW_WAKE_UP:
                            if zw_interpretation[ZW_COMMAND] == ZW_WAKE_UP_NOTIFICATION:
                                if devType == TRV or devType == VALVE:
                                    # As just a wakeup received - update TRV Controller device to ensure last TRV wakeup time recorded
                                    trvcDev.updateStateOnServer(key='zwaveEventReceivedDateTimeTrv', value=self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeTrv'])
                                elif devType == REMOTE:
                                    # As just a wakeup received - update TRV Controller device to ensure last Remote wakeup time recorded
                                    trvcDev.updateStateOnServer(key='zwaveEventReceivedDateTimeRemote', value=self.globals['trvc'][trvCtlrDevId]['zwaveEventReceivedDateTimeRemote'])
                                if self.globals['trvc'][trvCtlrDevId]['zwaveEventWakeUpSentDisplayFix'] != "":
                                    self.logger.debug(self.globals['trvc'][trvCtlrDevId]['zwaveEventWakeUpSentDisplayFix'])
                                    self.globals['trvc'][trvCtlrDevId]['zwaveEventWakeUpSentDisplayFix'] = u""

                zwave_report = f"\n\n{zwave_report_prefix}{zw_interpretation[ZW_INTERPRETATION_OVERVIEW_UI]}"
                zwave_report = f"{zwave_report}\n{zwave_report_prefix}{zw_interpretation[ZW_INTERPRETATION_DETAIL_UI]}{zwave_report_additional_detail}\n".encode('utf-8')

                self.logger.debug(zwave_report)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def zwaveCommandSent(self, zwave_command):

        try:
            zwave_report_prefix = f"{u'':-{u'^'}22}> Z-WAVE "  # 22 dashes as first part of prefix

            now_time = indigo.server.getTime()
            now_time_string = now_time.strftime('%Y-%m-%d %H:%M:%S')

            nodeId = zwave_command['nodeId']  # Can be None!

            zwave_report_additional_detail = u""

            trvCtlrDevId = 0
            zwave_event_wake_up_sent_display_fix = False

            if nodeId and nodeId in self.globals['zwave']['WatchList']:

                # Interpret Z-Wave Command
                zw_interpretation = self.globals[ZWI][ZWI_INSTANCE].interpret_zwave(False, zwave_command)  # True is to indicate Z-Wave Message sent

                if zw_interpretation is not None and zw_interpretation[ZW_INTERPRETATION_ATTEMPTED]:
                    # self.zwave_log(zw_interpretation[ZW_INDIGO_DEVICE], zw_interpretation[ZW_INTERPRETATION_OVERVIEW_UI], zw_interpretation[ZW_INTERPRETATION_DETAIL_UI])

                    address = zw_interpretation[ZW_NODE_ID]

                    if address in self.globals['zwave']['addressToDevice']:

                        # dev = indigo.devices[self.globals['zwave']['addressToDevice'][address]['devId']]  # TODO: IS THIS CORRECT / NEEDED ?
                        # devId = dev.id  # TODO: IS THIS CORRECT / NEEDED ?

                        devType = self.globals['zwave']['addressToDevice'][address]['type']
                        trvcDev = indigo.devices[self.globals['zwave']['addressToDevice'][address]['trvcId']]  # TRV Controller
                        trvCtlrDevId = trvcDev.id
                        if devType == TRV or devType == VALVE:
                            self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeTrv'] = now_time_string
                            if 'zwaveSentCountTrv' in self.globals['trvc'][trvCtlrDevId]:
                                self.globals['trvc'][trvCtlrDevId]['zwaveSentCountTrv'] += 1
                            else:
                                self.globals['trvc'][trvCtlrDevId]['zwaveSentCountTrv'] = 1
                            self.globals['trvc'][trvCtlrDevId]['zwaveLastSentCommandTrv'] = zw_interpretation[ZW_COMMAND_CLASS]
                        else:  # Must be Remote
                            self.globals['trvc'][trvCtlrDevId]['zwaveEventSentDateTimeRemote'] = now_time_string
                            if 'zwaveSentCountRemote' in self.globals['trvc'][trvCtlrDevId]:
                                self.globals['trvc'][trvCtlrDevId]['zwaveSentCountRemote'] += 1
                            else:
                                self.globals['trvc'][trvCtlrDevId]['zwaveSentCountRemote'] = 1
                            self.globals['trvc'][trvCtlrDevId]['zwaveLastSentCommandRemote'] = zw_interpretation[ZW_COMMAND_CLASS]

                        if zw_interpretation[ZW_COMMAND_CLASS] == ZW_THERMOSTAT_SETPOINT and zw_interpretation[ZW_COMMAND] == ZW_THERMOSTAT_SETPOINT_SET:
                            zwaveCommandSetpoint = zw_interpretation[ZW_VALUE]

                            if devType == TRV:
                                zwave_report_additional_detail = (
                                    f", Pending: {self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointFlag']}, Sequence:  '{self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointSequence']}', Setpoint: '{self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointValue']}'")

                                if self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointValue'] != zwaveCommandSetpoint:  # Assume  internally generated Z-Wave setpoint command
                                    # if self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointFlag']:  # if internally generated Z-Wave setpoint command reset flag
                                    #     self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointFlag'] = False  # Turn off
                                    # else:
                                    #     As not internally generated Z-Wave setpoint command, must be from UI
                                    self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_TRV_UI

                            else:  # Must be Remote as can't be a valve
                                zwave_report_additional_detail = (
                                    f", Pending: {self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointFlag']}, Sequence:  '{self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointSequence']}', Setpoint: '{self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointValue']}'")
                                if self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointFlag']:  # if internally generated Z-Wave setpoint command reset flag
                                    self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointFlag'] = False  # Turn off
                                else:
                                    # As not internally generated Z-Wave setpoint command, must be from UI
                                    self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_REMOTE_UI

                        elif zw_interpretation[ZW_COMMAND_CLASS] == ZW_SWITCH_MULTILEVEL:
                            if zw_interpretation[ZW_COMMAND] == ZW_SWITCH_MULTILEVEL_REPORT:
                                pass
                            elif zw_interpretation[ZW_COMMAND_CLASS] == ZW_SWITCH_MULTILEVEL_SET:
                                zwaveCommandValve = zw_interpretation[ZW_VALUE]
                                if zwaveCommandValve == 0:
                                    zwave_report_additional_detail = u", Closed"
                                else:
                                    zwave_report_additional_detail = f", Open {zw_interpretation[ZW_VALUE]}%"

                        elif zw_interpretation[ZW_COMMAND_CLASS] == ZW_THERMOSTAT_MODE and zw_interpretation[ZW_COMMAND] == ZW_THERMOSTAT_MODE_SET:
                            zwave_report_additional_detail = f", Mode = {zw_interpretation[ZW_MODE_UI]}"  # ERROR WAS HERE!!!

                            if self.globals['trvc'][trvCtlrDevId]['zwavePendingHvac']:  # if internally generated Z-Wave hvac command reset flag
                                self.globals['trvc'][trvCtlrDevId]['zwavePendingHvac'] = False  # Turn off
                            else:
                                pass
                                # As not internally generated Z-Wave hvac command, must be from UI
                                # if devType == TRV:
                                #     self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_TRV_UI
                                # else:  # Must be Remote as can't be a valve
                                #     self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_REMOTE_UI

                        elif zw_interpretation[ZW_COMMAND_CLASS] == ZWAVE_COMMAND_CLASS_WAKEUP:
                            zwave_event_wake_up_sent_display_fix = True

                zwave_report = f"\n\n{zwave_report_prefix}{zw_interpretation[ZW_INTERPRETATION_OVERVIEW_UI]}"
                zwave_report = f"{zwave_report}\n{zwave_report_prefix}{zw_interpretation[ZW_INTERPRETATION_DETAIL_UI]}{zwave_report_additional_detail}\n"
                if trvCtlrDevId != 0 and not zwave_event_wake_up_sent_display_fix:  # Not a Wakeup command - so output Z-Wave report
                    self.logger.debug(zwave_report)
                else:
                    self.globals['trvc'][trvCtlrDevId]['zwaveEventWakeUpSentDisplayFix'] = zwave_report

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    #################################
    #
    # Start of bespoke plugin methods
    #
    #################################

    def _showSchedule(self, trvCtlrDevId, scheduleReportLineLength):

        scheduleReport = ''

        trvcDev = indigo.devices[trvCtlrDevId]

        if trvcDev.enabled and trvcDev.configured:
            trvCtlrDevId = trvcDev.id
            scheduleReport = scheduleReport + self.boxLine(f'Device: \'{trvcDev.name}\'', scheduleReportLineLength, u'==')
            # scheduleList = collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['dynamic'].items()))

            ScheduleGroupList = [(collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['default'].items())), 'Default'),
                                 (collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['running'].items())), 'Running'),
                                 (collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['dynamic'].items())), 'Dynamic')]

            storedScheduleDefault = {}
            storedScheduleRunning = {}

            for scheduleList, scheduleType in ScheduleGroupList:
                if (scheduleType == 'Default' or scheduleType == 'Dynamic') and len(scheduleList) == 2:
                    continue
                elif scheduleType == 'Running' and len(scheduleList) == 2:
                    scheduleReport = scheduleReport + self.boxLine('  No schedules defined or enabled for device.', scheduleReportLineLength, u'==')
                    continue
                else:
                    scheduleReport = scheduleReport + self.boxLine(f'  Schedule Type: \'{scheduleType}\'', scheduleReportLineLength, u'==')

                previousScheduleId = 0
                previousScheduleTimeUi = ''  # To suppress PyCharm warning
                previousScheduleSetpoint = 0.0   # To suppress PyCharm warning
                for key, value in scheduleList.items():
                    # scheduleTime = int(key)
                    scheduleTimeUi = f'{value[0]}'
                    scheduleSetpoint = float(value[1])
                    scheduleId = value[2]
                    if scheduleId == 0:  # Ignore start entry (00:00)
                        continue
                    if previousScheduleId == 0 or previousScheduleId != scheduleId:
                        previousScheduleId = scheduleId
                        previousScheduleTimeUi = scheduleTimeUi
                        previousScheduleSetpoint = scheduleSetpoint
                    else:
                        scheduleEnabledName = f'schedule{previousScheduleId}Enabled'
                        scheduleActiveName = f'schedule{previousScheduleId}Active'

                        # self.logger.info(f'scheduleActiveName = {scheduleActiveName}, {self.globals["trvc"][trvCtlrDevId][scheduleActiveName]}')

                        if self.globals['trvc'][trvCtlrDevId][scheduleEnabledName]:
                            combinedScheduleTimesUi = f'{previousScheduleTimeUi} - {scheduleTimeUi}'
                            scheduleUi = f'Schedule {scheduleId}: {combinedScheduleTimesUi}. Setpoint = {previousScheduleSetpoint}'
                            # schedule = self.globals['trvc'][trvCtlrDevId]['schedule1TimeOn'] + ' - ' + self.globals['trvc'][trvCtlrDevId]['schedule1TimeOff']
                        else:
                            scheduleUi = f'Schedule {scheduleId}: Disabled'

                        if scheduleType == 'Default':
                            storedScheduleDefault[scheduleId] = scheduleUi
                        elif scheduleType == 'Running':
                            storedScheduleRunning[scheduleId] = scheduleUi
                            if storedScheduleDefault[scheduleId] != storedScheduleRunning[scheduleId]:
                                scheduleUi = f'{scheduleUi} [*]'
                        elif scheduleType == 'Dynamic':
                            if storedScheduleRunning[scheduleId] != scheduleUi:
                                scheduleUi = f'{scheduleUi} [*]'
                            if trvcDev.states[scheduleActiveName]:
                                scheduleUi = f'{scheduleUi} ACTIVE'

                        scheduleReport = scheduleReport + self.boxLine(f'    {scheduleUi}', scheduleReportLineLength, u'==')

        return scheduleReport

    def actionConfigApplyDefaultScheduleValues(self, valuesDict, typeId, actionId):

        self.logger.debug(f'actionConfigApplyDefaultScheduleValues: typeId[{typeId}], actionId[{actionId}], ValuesDict:\n{valuesDict}\'')

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

    def boxLine(self, info, lineLength, boxCharacters):   # noqa - Method is not declared static

        fillLength = lineLength - len(info) - 1 - (2 * len(boxCharacters))
        if fillLength < 0:
            return boxCharacters + f'\n LINE LENGTH {lineLength} TOO SMALL FOR BOX CHARACTERS \'{boxCharacters}\' AND INFORMATION \'{info}\''

        # lenBoxCharacters = len(boxCharacters)
        updatedLine = f'\n{boxCharacters} {info}{(" " * fillLength)}{boxCharacters}'
        return updatedLine

    def deviceRaceConditionReEnableTriggered(self, trvCtlrDevId):

        try:
            if trvCtlrDevId in self.globals['timers']['raceCondition']:
                self.globals['timers']['raceCondition'][trvCtlrDevId].cancel()

            self.logger.error(f'Re-Enabling TRV Controller \'{indigo.devices[trvCtlrDevId].name}\' following potential race condition detection (which as a result the device was disabled).')
            indigo.device.enable(trvCtlrDevId, value=True)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    # noinspection PyUnusedLocal
    def heatSourceControllerDevices(self, indigo_filter="", valuesDict=None, typeId="", targetId=0):

        array = []
        for dev in indigo.devices:
            if self.globals['config']['disableHeatSourceDeviceListFilter']:
                try:
                    if dev.deviceTypeId == 'zwThermostatType' or dev.deviceTypeId == 'zwRelayType' or dev.deviceTypeId == 'pseudoRelay':
                        if dev.model not in self.globals['supportedTrvModels']:
                            array.append((dev.id, dev.name))
                except Exception:
                    pass
            else:
                if dev.model in self.globals['supportedHeatSourceControllers']:
                    array.append((dev.id, dev.name))

        arraySorted = sorted(array, key=lambda dev_name: dev_name[1].lower())  # sort by device name
        arraySorted.insert(0, (0, 'NO HEAT SOURCE DEVICE '))
        arraySorted.insert(0, (-1, '-- Select Device Heat Source --'))

        return arraySorted

    # noinspection PyUnusedLocal
    def heatSourceControllerVariables(self, indigo_filter="", valuesDict=None, typeId="", targetId=0):

        array = []
        for var in indigo.variables:
            if self.globals['config']['trvVariableFolderId'] == 0:
                array.append((var.id, var.name))
            else:
                if var.folderId == self.globals['config']['trvVariableFolderId']:
                    array.append((var.id, var.name))

        arraySorted = sorted(array, key=lambda var_name: var_name[1].lower())  # sort by variable name
        arraySorted.insert(0, (0, 'NO HEAT SOURCE VARIABLE'))
        arraySorted.insert(0, (-1, '-- Select Variable Heat Source --'))

        return arraySorted

    def listActiveDebugging(self, monitorDebugTypes):   # noqa - Method is not declared static

        loop = 0
        listedTypes = ''
        for monitorDebugType in monitorDebugTypes:
            if loop == 0:
                listedTypes = listedTypes + monitorDebugType
            else:
                listedTypes = listedTypes + ', ' + monitorDebugType
            loop += 1
        return listedTypes

    def processToggleTurnOnOff(self, pluginAction, dev):

        if float(self.globals['trvc'][dev.id]['setpointHeat']) == float(self.globals['trvc'][dev.id]['setpointHeatMinimum']):
            self.processTurnOn(pluginAction, dev)
        else:
            self.processTurnOff(pluginAction, dev)

    # noinspection PyUnusedLocal
    def processTurnOff(self, pluginAction, dev):

        trvCtlrDevId = dev.id
        newSetpoint = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum'])

        # keyValueList = [
        # {'key': 'controllerMode', 'value': CONTROLLER_MODE_UI},
        # {'key': 'controllerModeUi', 'value':  CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_UI]},
        # {'key': 'setpointHeat', 'value': newSetpoint}
        #     ]
        # dev.updateStatesOnServer(keyValueList)

        queuedCommand = CMD_UPDATE_TRV_CONTROLLER_STATES
        updateList = dict()
        updateList[UPDATE_CONTROLLER_HEAT_SETPOINT] = newSetpoint
        updateList[UPDATE_CONTROLLER_MODE] = CONTROLLER_MODE_UI
        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, self.globals['deviceUpdatedSequenceCount'], queuedCommand, trvCtlrDevId, [updateList, ]])

    # noinspection PyUnusedLocal
    def processTurnOn(self, pluginAction, dev):

        trvCtlrDevId = dev.id
        newSetpoint = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatDefault'])

        # keyValueList = [
        # {'key': 'controllerMode', 'value': CONTROLLER_MODE_UI},
        # {'key': 'controllerModeUi', 'value':  CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_UI]},
        # {'key': 'setpointHeat', 'value': newSetpoint}
        #     ]
        # dev.updateStatesOnServer(keyValueList)

        queuedCommand = CMD_UPDATE_TRV_CONTROLLER_STATES
        updateList = dict()
        updateList[UPDATE_CONTROLLER_HEAT_SETPOINT] = newSetpoint
        updateList[UPDATE_CONTROLLER_MODE] = CONTROLLER_MODE_UI
        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, self.globals['deviceUpdatedSequenceCount'], queuedCommand, trvCtlrDevId, [updateList, ]])

    # noinspection PyUnusedLocal
    def processAdvance(self, pluginAction, dev):

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_ADVANCE, dev.id, [ADVANCE_NEXT]])

    # noinspection PyUnusedLocal
    def processAdvanceOff(self, pluginAction, dev):

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_ADVANCE, dev.id, [ADVANCE_NEXT_OFF]])

    def processAdvanceOffToggle(self, pluginAction, dev):

        if not self.globals['trvc'][dev.id]['advanceActive']:
            self.processAdvanceOff(pluginAction, dev)
        else:
            self.processCancelAdvance(pluginAction, dev)

    # noinspection PyUnusedLocal
    def processAdvanceOn(self, pluginAction, dev):

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_ADVANCE, dev.id, [ADVANCE_NEXT_ON]])

    def processAdvanceOnToggle(self, pluginAction, dev):

        if not self.globals['trvc'][dev.id]['advanceActive']:
            self.processAdvanceOn(pluginAction, dev)
        else:
            self.processCancelAdvance(pluginAction, dev)

    def processAdvanceToggle(self, pluginAction, dev):

        if not self.globals['trvc'][dev.id]['advanceActive']:
            self.processAdvance(pluginAction, dev)
        else:
            self.processCancelAdvance(pluginAction, dev)

    # noinspection PyUnusedLocal
    def processCancelAdvance(self, pluginAction, dev):

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_ADVANCE_CANCEL, dev.id, [True]])

    def processBoost(self, pluginAction, dev):

        if pluginAction.pluginTypeId == 'processBoost':
            boostMode = int(pluginAction.props.get('boostMode', 0))
        elif pluginAction.pluginTypeId == 'processBoostToggle':
            boostMode = int(pluginAction.props.get('toggleBoostMode', 0))
        else:
            self.logger.error(f'Boost logic failure for thermostat \'{dev.name}\' - boost not actioned for id \'{pluginAction}\'')
            return

        if boostMode == BOOST_MODE_SELECT:
            self.logger.error(f'Boost Mode not set for thermostat \'{dev.name}\' - boost not actioned')
            return

        if pluginAction.pluginTypeId == 'processBoost':
            boostDeltaT = float(pluginAction.props.get('boostDeltaT', 2.0))
            boostSetpoint = float(pluginAction.props.get('boostSetpoint', 21.0))
            boostMinutes = int(pluginAction.props.get('boostMinutes', 20))
        else:  # Must be pluginAction = processBoostToggle
            boostDeltaT = float(pluginAction.props.get('toggleBoostDeltaT', 2.0))
            boostSetpoint = float(pluginAction.props.get('toggleBoostSetpoint', 21.0))
            boostMinutes = int(pluginAction.props.get('toggleBoostMinutes', 20))

        self.globals['trvc'][dev.id]['boostActive'] = True

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_BOOST, dev.id, [boostMode, boostDeltaT, boostSetpoint, boostMinutes]])

        if boostMode == BOOST_MODE_DELTA_T:
            self.logger.info(f'Boost actioned for {boostMinutes} minutes with a Delta T of {boostDeltaT} degrees for thermostat \'{dev.name}\'')
        else:  # BOOST_MODE_SETPOINT
            self.logger.info(f'Boost actioned for {boostMinutes} minutes with a Setpoint of {boostSetpoint} degrees for thermostat \'{dev.name}\'')

    def processBoostToggle(self, pluginAction, dev):

        if not self.globals['trvc'][dev.id]['boostActive']:
            self.processBoost(pluginAction, dev)
        else:
            self.processCancelBoost(pluginAction, dev)

    # noinspection PyUnusedLocal
    def processCancelBoost(self, pluginAction, dev):

        if self.globals['trvc'][dev.id]['boostActive']:
            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_BOOST_CANCEL, dev.id, [True]])
            self.logger.info(f'Boost cancelled for thermostat \'{dev.name}\'')
        else:
            self.logger.info(f'Boost cancel request ignored for thermostat \'{dev.name}\' as no Boost active')

    def processExtend(self, pluginAction, dev):

        extendIncrementMinutes = int(pluginAction.props.get('extendIncrementMinutes', 15))
        extendMaximumMinutes = int(pluginAction.props.get('extendMaximumMinutes', 15))

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_EXTEND, dev.id, [extendIncrementMinutes, extendMaximumMinutes]])

        # self.logger.info(f'Extend actioned for thermostat \'{dev.name}\'')

    # noinspection PyUnusedLocal
    def processCancelExtend(self, pluginAction, dev):

        if self.globals['trvc'][dev.id]['extendActive']:
            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_EXTEND_CANCEL, dev.id, [True]])
            # self.logger.info(f'Extend cancelled for thermostat \'{dev.name}\'')
        else:
            self.logger.info(f'Extend cancel request ignored for thermostat \'{dev.name}\' as no Extend active')

    def processResetScheduleToDeviceDefaults(self, pluginAction, dev):

        self.logger.debug(f' Thermostat \'{dev.name}\', Action received: \'{pluginAction.description}\'')
        self.logger.debug(f'... Action details:\n{pluginAction}\n')

        devId = dev.id

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_RESET_SCHEDULE_TO_DEVICE_DEFAULTS, devId, None])

    # noinspection PyUnusedLocal
    def processShowAllSchedules(self, pluginAction):

        scheduleReportLineLength = 80
        scheduleReport = f'\n{"=" * scheduleReportLineLength}'
        scheduleReport = scheduleReport + self.boxLine('TRV Controller Plugin - Heating Schedules', scheduleReportLineLength, u'==')
        scheduleReport = scheduleReport + self.boxLine(' ', scheduleReportLineLength, u'==')

        for trvcDev in indigo.devices.iter("self"):
            scheduleReport = scheduleReport + self._showSchedule(trvcDev.id, scheduleReportLineLength)

        scheduleReport = scheduleReport + self.boxLine(' ', scheduleReportLineLength, u'==')
        scheduleReport = scheduleReport + f'\n{"=" * scheduleReportLineLength}\n'

        self.logger.info(scheduleReport)

    # noinspection PyUnusedLocal
    def processShowSchedule(self, pluginAction, trvcDev):

        try:
            scheduleReportLineLength = 80
            scheduleReport = f'\n{"=" * scheduleReportLineLength}'
            scheduleReport = scheduleReport + self.boxLine('TRV Controller Plugin - Heating Schedule', scheduleReportLineLength, u'==')
            scheduleReport = scheduleReport + self.boxLine(' ', scheduleReportLineLength, u'==')
            scheduleReport = scheduleReport + self._showSchedule(trvcDev.id, scheduleReportLineLength)
            scheduleReport = scheduleReport + self.boxLine(' ', scheduleReportLineLength, u'==')
            scheduleReport = scheduleReport + f'\n{"=" * scheduleReportLineLength}\n'

            self.logger.info(scheduleReport)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    # noinspection PyUnusedLocal
    def processShowStatus(self, pluginAction, dev):

        devId = dev.id
        self.logger.info(f'Showing full internal status of \'{dev.name}\'')
        for self.key in sorted(self.globals['trvc'][devId].keys()):
            self.logger.info(f'\'{dev.name}\': {self.key} = {self.globals["trvc"][devId][self.key]}')
        self.logger.info("Showing Heat SourceTRV Controller Device Table")
        for dev in self.globals['devicesToTrvControllerTable'].items():
            self.logger.info(f"Device: {dev}")

    # noinspection PyUnusedLocal
    def processShowZwaveWakeupInterval(self, pluginAction):

        statusOptimize = dict()
        for dev in indigo.devices.iter("self"):
            if dev.enabled and dev.configured:
                devId = dev.id

                if self.globals['trvc'][devId]['zwaveDeltaCurrent'] != "[n/a]":
                    tempSplit = self.globals['trvc'][devId]['zwaveDeltaCurrent'].split(':')
                    tempZwaveDeltaCurrent = int(tempSplit[0]) * 60 + int(tempSplit[1])
                    # tempZwaveDeltaCurrent = datetime.datetime.strptime(self.globals['trvc'][devId]['zwaveDeltaCurrent'], '%M:%S')
                    tempA, tempB = divmod(tempZwaveDeltaCurrent, 300)
                    statusOptimize[dev.name] = int(tempB)

        self.logger.info("Z-wave wakeup intervals between TRVs (in seconds):")
        optimizeDifference = 0
        sortedItems = sorted(statusOptimize.items(), key=operator.itemgetter(1, 0))
        for item1 in sortedItems:
            if optimizeDifference == 0:  # Ensure Intervals start at zero
                optimizeDifference = int(item1[1])
            optimizeDifferenceCalc = int(item1[1] - optimizeDifference)
            self.logger.info("  %s = %s [Interval = %s]" % (item1[0], str("  " + str(item1[1]))[-3:], str("  " + str(optimizeDifferenceCalc))[-3:]))
            optimizeDifference = int(item1[1])

    # noinspection PyUnusedLocal
    def processUpdateAllCsvFiles(self, pluginAction, trvCtlrDev):

        trvCtlrDevId = trvCtlrDev.id

        try:
            if self.globals['config']['csvStandardEnabled']:
                if self.globals['trvc'][trvCtlrDevId]['updateCsvFile']:
                    self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_LOW, 0, CMD_UPDATE_ALL_CSV_FILES, trvCtlrDevId, None])
                else:
                    self.logger.error(f'Request to update All CSV Files ignored as option \'On State Change [...]\' not set for \'{trvCtlrDev.name}\' in its device settings.')
            else:
                self.logger.error(f'Request to update All CSV Files ignored for \'{trvCtlrDev.name}\' as option \'Enable Standard CSV\' not enabled in the plugin config.')

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def processUpdateAllCsvFilesViaPostgreSQL(self, pluginAction, trvCtlrDev):
 
        trvCtlrDevId = trvCtlrDev.id

        try:
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
                    self.logger.error(f'Request to update All CSV Files Via PostgreSQL ignored as option \'Enable PostgreSQL CSV\' not set for \'{trvCtlrDev.name}\' in its device settings.')
            else:
                self.logger.error(f'Request to update All CSV Files Via PostgreSQL ignored for \'{trvCtlrDev.name}\' as option \'Enable PostgreSQL CSV\' not enabled in the plugin config.')

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def processUpdateSchedule(self, pluginAction, dev):

        self.logger.debug(f' Thermostat \'{dev.name}\', Action received: \'{pluginAction.description}\'')
        self.logger.debug(f'... Action details:\n{pluginAction}\n')

        devId = dev.id

        self.globals['trvc'][devId]['nextScheduleExecutionTime'] = 'Not yet evaluated'
        self.globals['trvc'][devId]['schedule1Enabled'] = bool(pluginAction.props.get('schedule1Enabled', False))
        self.globals['trvc'][devId]['schedule1TimeOn']  = pluginAction.props.get('schedule1TimeOn', '00:00')
        self.globals['trvc'][devId]['schedule1TimeOff'] = pluginAction.props.get('schedule1TimeOff', '00:00')
        self.globals['trvc'][devId]['schedule1SetpointHeat'] = pluginAction.props.get('schedule1SetpointHeat', 0.00)
        self.globals['trvc'][devId]['schedule2Enabled'] = bool(pluginAction.props.get('schedule2Enabled', False))
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
            self.globals['trvc'][devId]['schedule1SetpointHeatUi'] = f'{self.globals["trvc"][devId]["schedule1SetpointHeat"]} °C'
            self.globals['trvc'][devId]['schedule1TimeUi'] = f'{self.globals["trvc"][devId]["schedule1TimeOn"]} - {self.globals["trvc"][devId]["schedule1TimeOff"]}'

        if not self.globals['trvc'][devId]['schedule2Enabled'] or self.globals['trvc'][devId]['schedule2SetpointHeat'] == 0.0:
            self.globals['trvc'][devId]['schedule2SetpointHeatUi'] = 'Not Set'
            self.globals['trvc'][devId]['schedule2TimeUi'] = 'Inactive'
        else:
            self.globals['trvc'][devId]['schedule2SetpointHeatUi'] = f'{self.globals["trvc"][devId]["schedule2SetpointHeat"]} °C'
            self.globals['trvc'][devId]['schedule2TimeUi'] = f'{self.globals["trvc"][devId]["schedule2TimeOn"]} - {self.globals["trvc"][devId]["schedule2TimeOff"]}'

        if not self.globals['trvc'][devId]['schedule3Enabled'] or self.globals['trvc'][devId]['schedule3SetpointHeat'] == 0.0:
            self.globals['trvc'][devId]['schedule3SetpointHeatUi'] = 'Not Set'
            self.globals['trvc'][devId]['schedule3TimeUi'] = 'Inactive'
        else:
            self.globals['trvc'][devId]['schedule3SetpointHeatUi'] = f'{self.globals["trvc"][devId]["schedule3SetpointHeat"]} °C'
            self.globals['trvc'][devId]['schedule3TimeUi'] = f'{self.globals["trvc"][devId]["schedule3TimeOn"]} - {self.globals["trvc"][devId]["schedule3TimeOff"]}'

        if not self.globals['trvc'][devId]['schedule4Enabled'] or self.globals['trvc'][devId]['schedule4SetpointHeat'] == 0.0:
            self.globals['trvc'][devId]['schedule4SetpointHeatUi'] = 'Not Set'
            self.globals['trvc'][devId]['schedule4TimeUi'] = 'Inactive'
        else:
            self.globals['trvc'][devId]['schedule4SetpointHeatUi'] = f'{self.globals["trvc"][devId]["schedule4SetpointHeat"]} °C'
            self.globals['trvc'][devId]['schedule4TimeUi'] = f'{self.globals["trvc"][devId]["schedule4TimeOn"]} - {self.globals["trvc"][devId]["schedule4TimeOff"]}'

        keyValueList = [
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
        dev.updateStatesOnServer(keyValueList)

        # Set-up schedules
        self.globals['schedules'][devId]['running'] = dict()
        scheduleSetpointOff = float(self.globals['trvc'][devId]['setpointHeatMinimum'])
        self.globals['schedules'][devId]['running'][0] = ('00:00', scheduleSetpointOff, 0, False)  # Start of Day
        self.globals['schedules'][devId]['running'][240000] = ('24:00', scheduleSetpointOff, 9, False)  # End of Day

        if self.globals['trvc'][devId]['schedule1Enabled']:
            scheduleTimeOnUi = self.globals['trvc'][devId]['schedule1TimeOn']
            scheduleTimeOn = int(scheduleTimeOnUi.replace(':', '')) * 100  # Add in Seconds
            scheduleTimeOffUi = self.globals['trvc'][devId]['schedule1TimeOff']
            scheduleTimeOff = int(scheduleTimeOffUi.replace(':', '')) * 100  # Add in Seconds
            scheduleSetpointOn = float(self.globals['trvc'][devId]['schedule1SetpointHeat'])
            self.globals['schedules'][devId]['running'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 1, True)
            self.globals['schedules'][devId]['running'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 1, False)

        if self.globals['trvc'][devId]['schedule2Enabled']:
            scheduleTimeOnUi = self.globals['trvc'][devId]['schedule2TimeOn']
            scheduleTimeOn = int(scheduleTimeOnUi.replace(':', '')) * 100  # Add in Seconds
            scheduleTimeOffUi = self.globals['trvc'][devId]['schedule2TimeOff']
            scheduleTimeOff = int(scheduleTimeOffUi.replace(':', '')) * 100  # Add in Seconds
            scheduleSetpointOn = float(self.globals['trvc'][devId]['schedule2SetpointHeat'])
            self.globals['schedules'][devId]['running'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 2, True)
            self.globals['schedules'][devId]['running'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 2, False)

        if self.globals['trvc'][devId]['schedule3Enabled']:
            scheduleTimeOnUi = self.globals['trvc'][devId]['schedule3TimeOn']
            scheduleTimeOn = int(scheduleTimeOnUi.replace(':', '')) * 100  # Add in Seconds
            scheduleTimeOffUi = self.globals['trvc'][devId]['schedule3TimeOff']
            scheduleTimeOff = int(scheduleTimeOffUi.replace(':', '')) * 100  # Add in Seconds
            scheduleSetpointOn = float(self.globals['trvc'][devId]['schedule3SetpointHeat'])
            self.globals['schedules'][devId]['running'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 3, True)
            self.globals['schedules'][devId]['running'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 3, False)

        if self.globals['trvc'][devId]['schedule4Enabled']:
            scheduleTimeOnUi = self.globals['trvc'][devId]['schedule4TimeOn']
            scheduleTimeOn = int(scheduleTimeOnUi.replace(':', '')) * 100  # Add in Seconds
            scheduleTimeOffUi = self.globals['trvc'][devId]['schedule4TimeOff']
            scheduleTimeOff = int(scheduleTimeOffUi.replace(':', '')) * 100  # Add in Seconds
            scheduleSetpointOn = float(self.globals['trvc'][devId]['schedule4SetpointHeat'])
            self.globals['schedules'][devId]['running'][scheduleTimeOn] = (scheduleTimeOnUi, scheduleSetpointOn, 4, True)
            self.globals['schedules'][devId]['running'][scheduleTimeOff] = (scheduleTimeOffUi, scheduleSetpointOff, 4, False)

        self.globals['schedules'][devId]['running'] = collections.OrderedDict(sorted(self.globals['schedules'][devId]['running'].items()))
        self.globals['schedules'][devId]['dynamic'] = self.globals['schedules'][devId]['running'].copy()

        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_DELAY_COMMAND, devId, [CMD_PROCESS_HEATING_SCHEDULE, 2.0, None]])

    def remoteThermostatDevices(self, indigo_filter="", valuesDict=None, typeId="", targetId=0):   # noqa - Method is not declared static + unused local symbols

        array = []
        for dev in indigo.devices.iter():
            if dev.deviceTypeId != 'trvController':
                # Deprecated 'subModel' code follows ...
                # if (dev.subModel == 'Temperature' or dev.subModel == 'Temperature 1' or dev.subModel == 'Thermostat' or dev.deviceTypeId == 'hueMotionTemperatureSensor' or
                #         (dev.model == 'Thermostat (TF021)' and dev.subModel[0:7].lower() == 'sensor ')):

                # if dev.subType == 'Temperature' or dev.subType == 'Thermostat' or (dev.model == 'Thermostat (TF021)' and dev.subType == 'Sensor'):
                #     array.append((dev.id, dev.name))
                # else:
                #     try:
                #         test = float(dev.states['temperatureInput1'])  # noqa [test value not used] - e.g. Secure SRT321 / HRT4-ZW
                #     except (AttributeError, KeyError, ValueError):
                #         try:
                #             test = float(dev.states['temperature'])  # noqa [test value not used] -  e.g. Oregon Scientific Temp Sensor
                #         except (AttributeError, KeyError, ValueError):
                #             try:
                #                 test = float(dev.states['Temperature'])  # noqa [test value not used] -  e.g. Netatmo
                #             except (AttributeError, KeyError, ValueError):
                #                 try:
                #                     test = float(dev.states['sensorValue'])  # noqa [test value not used] -  e.g. HeatIT TF021 / MQTT Value Sensor Device
                #                 except (AttributeError, KeyError, ValueError):
                #                     continue
                #     array.append((dev.id, dev.name))

                if type(dev) == indigo.ThermostatDevice or type(dev) == indigo.SensorDevice:
                    num_temperature_inputs = int(dev.ownerProps.get("NumTemperatureInputs", "0"))
                    if num_temperature_inputs > 0:
                        array.append((dev.id, dev.name))

        return sorted(array, key=lambda dev_name: dev_name[1].lower())  # sort by device name

    def radiatorTemperatureSensorDevices(self, indigo_filter="", valuesDict=None, typeId="", targetId=0):   # noqa - Method is not declared static + unused local symbols

        array = []
        for dev in indigo.devices.iter():
            if dev.deviceTypeId != 'trvController':
                if type(dev) == indigo.ThermostatDevice or type(dev) == indigo.SensorDevice:
                    num_temperature_inputs = int(dev.ownerProps.get("NumTemperatureInputs", "0"))
                    if num_temperature_inputs > 0:
                        array.append((dev.id, dev.name))

        return sorted(array, key=lambda dev_name: dev_name[1].lower())  # sort by device name

    # noinspection PyUnusedLocal
    def restateSchedulesTriggered(self, triggeredSeconds):
        try:
            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_RESTATE_SCHEDULES, None, None])

            secondsUntilSchedulesRestated = calculateSecondsUntilSchedulesRestated()
            self.globals['timers']['reStateSchedules'] = threading.Timer(float(secondsUntilSchedulesRestated), self.restateSchedulesTriggered, [secondsUntilSchedulesRestated])
            self.globals['timers']['reStateSchedules'].daemon = True
            self.globals['timers']['reStateSchedules'].start()

            self.logger.info(f'TRV Controller has calculated the number of seconds until Schedules restated as {secondsUntilSchedulesRestated}')

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    # noinspection PyUnusedLocal
    def trvControlledDevices(self, indigo_filter="", valuesDict=None, typeId="", targetId=0): # noqa
        array = []
        for dev in indigo.devices.iter("indigo.thermostat"):
            if dev.deviceTypeId != 'trvController':
                array.append((dev.id, dev.name))
        return sorted(array, key=lambda dev_name: dev_name[1].lower())   # sort by device name

    # noinspection PyUnusedLocal
    def trvThermostatDeviceSelected(self, valuesDict, typeId, devId):

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
            valuesDict['trvDeviceSetpointHeatMaximum'] = self.globals['supportedTrvModels'][trv_model_name]['setpointHeatMaximum']

        return valuesDict

    def validateSchedule(self, trvcId, valuesDict, scheduleNumber):
        # Common routine to check a schedule: On time, off time and heat setpoint
        # Used by validateDeviceConfigUi

        try:
            # setup names
            scheduleTimeOnName = f'schedule{scheduleNumber}TimeOn'
            scheduleTimeOffName = f'schedule{scheduleNumber}TimeOff'
            scheduleSetpointHeatName = f'schedule{scheduleNumber}SetpointHeat'

            # self.logger.error(f'validateSchedule: OnName = \'{scheduleTimeOnName}\', OffName = \'{scheduleTimeOffName}\', SetpointHeatName = \'{scheduleSetpointHeatName}\'')

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
                except Exception:
                    return '24:00'

            # Validate Schedule ON time
            scheduleTimeOn = '24:00'
            try:
                scheduleTimeToTest = valuesDict.get(scheduleTimeOnName, '24:00')
                scheduleTimeOn = validateTime(scheduleTimeToTest)
            except Exception:
                pass
            if scheduleTimeOn == '00:00' or scheduleTimeOn == '24:00':
                errorDict = indigo.Dict()
                errorDict[scheduleTimeOnName] = 'Set time (in HH:MM format) between 00:01 and 23:59 (inclusive)'
                errorDict['showAlertText'] = f'You must enter a Schedule {scheduleName} time (in HH:MM format) between 00:01 and 23:59 (inclusive) for when the TRV will turn ON.'
                return False, errorDict

            # Validate Schedule OFF time
            scheduleTimeOff = '24:00'
            try:
                scheduleTimeToTest = valuesDict.get(scheduleTimeOffName, '24:00')
                scheduleTimeOff = validateTime(scheduleTimeToTest)
            except Exception:
                pass
            if scheduleTimeOff == '00:00' or scheduleTimeOff == '24:00':
                errorDict = indigo.Dict()
                errorDict[scheduleTimeOffName] = 'Set time (in HH:MM format) between 00:01 and 23:59 (inclusive)'
                errorDict['showAlertText'] = f'You must enter a Schedule {scheduleName} time (in HH:MM format) between 00:01 and 23:59 (inclusive) for when the TRV will turn OFF.'
                return False, errorDict

            # Validate Schedule 1 Heat Setpoint

            setpointHeatMinimum = float(valuesDict.get('setpointHeatMinimum', 0.0))
            setpointHeatMaximum = float(valuesDict.get('setpointHeatMaximum', 0.0))

            if setpointHeatMinimum == 0.0 or setpointHeatMaximum == 0.0:
                errorDict = indigo.Dict()
                errorDict[scheduleSetpointHeatName] = 'TRV Maximum and Minimum Setpoint Heat Temperatures invalid - make sure to select TRV Thermostat Device before defining schedule'
                errorDict['showAlertText'] = f'TRV Maximum and Minimum Setpoint Heat Temperatures invalid for Schedule {scheduleName}, make sure to select TRV Thermostat Device before defining schedule'
                return False, errorDict

            valid = False
            try:
                scheduleSetpointHeat = float(valuesDict.get(scheduleSetpointHeatName, 0))
                valid = True
            except ValueError:
                scheduleSetpointHeat = 0  # To suppress PyCharm warning

            if valid:  # so far!
                if scheduleSetpointHeat < setpointHeatMinimum or scheduleSetpointHeat > setpointHeatMaximum or scheduleSetpointHeat % 0.5 != 0:
                    valid = False

            if not valid:
                errorDict = indigo.Dict()
                errorDict[scheduleSetpointHeatName] = f'Setpoint temperature must be numeric and set between {setpointHeatMinimum} and {setpointHeatMaximum} (inclusive)'
                errorDict['showAlertText'] = f'You must enter a valid Schedule {scheduleName} Setpoint temperature for the TRV. It must be numeric and set between {setpointHeatMinimum} and {setpointHeatMaximum} (inclusive) and a multiple of 0.5.'
                return False, errorDict

            # Check Schedule Times consistent
            if scheduleTimeOff > scheduleTimeOn:
                secondsDelta = secondsFromHHMM(scheduleTimeOff) - secondsFromHHMM(scheduleTimeOn)
            else:
                secondsDelta = 0
        
            if secondsDelta < 600:  # 10 minutes (600 seconds) check
                errorDict = indigo.Dict()
                errorDict[scheduleTimeOnName] = f'The Schedule {scheduleName} ON time must be at least 10 minutes before the Schedule {scheduleName} OFF time'
                errorDict[scheduleTimeOffName] = f'The Schedule {scheduleName} OFF time must be at least 10 minutes after the Schedule {scheduleName} ON time'
                errorDict['showAlertText'] = f'The Schedule {scheduleName} ON time [{scheduleTimeOn}] must be at least 10 minutes before the Schedule {scheduleName} OFF time [{scheduleTimeOff}]'
                return False, errorDict

            return True, [scheduleTimeOn, scheduleTimeOff, scheduleSetpointHeat]

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def zwaveWakeupMissedTriggered(self, trvCtlrDevId, devType, devId):
        try:
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

            if not indigo.devices[trvCtlrDevId].enabled:
                self.logger.warning(
                    f'Z-Wave wakeup check cancelled for disabled Controller \'{indigo.devices[trvCtlrDevId].name}\' and associated {deviceType} \'{indigo.devices[devId].name}\'. Last Z-wave command received: {lastWakeupTime}')
                return

            if not indigo.devices[devId].enabled:
                self.logger.warning(
                    f'Z-Wave wakeup check cancelled for disabled {deviceType} \'{indigo.devices[devId].name}\', controlled by \'{indigo.devices[trvCtlrDevId].name}\'. Last Z-wave command received: {lastWakeupTime}')
                return

            indigo.devices[trvCtlrDevId].updateStateImageOnServer(indigo.kStateImageSel.TimerOn)

            self.logger.warning(
                f'Z-Wave wakeup missed for {deviceType} \'{indigo.devices[devId].name}\', controlled by \'{indigo.devices[trvCtlrDevId].name}\'. Last Z-wave command received: {lastWakeupTime}')

            self.globals['timers']['zwaveWakeupCheck'][devId] = threading.Timer(float(nextWakeupMissedSeconds), self.zwaveWakeupMissedTriggered, [trvCtlrDevId, devType, devId])
            self.globals['timers']['zwaveWakeupCheck'][devId].daemon = True
            self.globals['timers']['zwaveWakeupCheck'][devId].start()

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement
