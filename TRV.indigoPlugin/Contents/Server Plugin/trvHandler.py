#! /usr/bin/env python
# -*- coding: utf-8 -*-
#
# TRV Controller © Autolog 2018 - 2022
#

try:
    # noinspection PyUnresolvedReferences
    import indigo
except ImportError:
    pass

import collections
import datetime

import postgresql
import queue
import sys
import threading
import time
import traceback

from constants import *


# noinspection PyPep8Naming
def calcSeconds(schedule_time, now_time):
    # noinspection PyPep8Naming
    def evaluate_seconds(argument_time):  # e.g.: 141545
        time_hours = argument_time // 10000  # e.g.: 14
        time_minutes_seconds = argument_time % 10000  # e.g. 1545
        time_minutes = time_minutes_seconds // 100  # e.g.: 15
        time_seconds = time_minutes_seconds % 100  # e.g.: 45
        evaluated_seconds = (time_hours * 3600) + (time_minutes * 60) + time_seconds
        return evaluated_seconds

    schedule_seconds = evaluate_seconds(schedule_time)
    now_seconds = evaluate_seconds(now_time)

    if now_seconds < schedule_seconds:
        result = schedule_seconds - now_seconds

        result_hours = result // 3600
        result_minutes_seconds = result % 3600
        result_minutes = result_minutes_seconds // 60
        result_seconds = result_minutes_seconds % 60
        result_log = (
            f'Time to next schedule at {schedule_time} [{schedule_seconds}] from now {now_time} [{now_seconds}]: Seconds = {result} = HH = {result_hours}, MM = {result_minutes}, SS = {result_seconds}')
    else:
        result = 0.0
        result_log = f'Nothing to immediately schedule: Time to next schedule at {schedule_time} [{schedule_seconds}] from now {now_time} [{now_seconds}]'

    return result, result_log


# noinspection PyPep8Naming
def calculateSecondsSinceMidnight():
    # Calculate number of seconds until five minutes after next midnight 

    today = datetime.datetime.now() - datetime.timedelta(1)
    midnight = datetime.datetime(year=today.year, month=today.month, day=today.day, hour=0, minute=0, second=0)
    seconds_since_midnight = int((datetime.datetime.now() - midnight).seconds)  # Seconds since midnight

    return seconds_since_midnight


# noinspection PyUnresolvedReferences, PyPep8Naming
class ThreadTrvHandler(threading.Thread):

    # This class handles TRV processing

    def __init__(self, pluginGlobals, event):

        threading.Thread.__init__(self)

        self.globals = pluginGlobals

        self.trvHandlerLogger = logging.getLogger("Plugin.TRV_H")
        self.trvHandlerLogger.debug("Debugging TRV Handler Thread")

        self.threadStop = event

    def exception_handler(self, exception_error_message, log_failing_statement):
        filename, line_number, method, statement = traceback.extract_tb(sys.exc_info()[2])[-1]
        module = filename.split('/')
        log_message = f"'{exception_error_message}' in module '{module[-1]}', method '{method}'"
        if log_failing_statement:
            log_message = log_message + f"\n   Failing statement [line {line_number}]: '{statement}'"
        else:
            log_message = log_message + f" at line {line_number}"
        self.trvHandlerLogger.error(log_message)

    def run(self):

        try:
            # Initialise routine on thread start
            self.trvHandlerLogger.debug('TRV Handler Thread initialised')

            while not self.threadStop.is_set():
                try:
                    trvQueuedEntry = self.globals['queues']['trvHandler'].get(True, 5)

                    # trvQueuedEntry format:
                    #   - Priority
                    #   - Command
                    #   - Device
                    #   - Data

                    # self.trvHandlerLogger.debug(f'DEQUEUED MESSAGE = {trvQueuedEntry}')
                    trvQueuePriority, trvQueueSequence, trvCommand, trvCommandDevId, trvCommandPackage = trvQueuedEntry

                    if trvCommand == CMD_STOP_THREAD:
                        break  # Exit While loop and quit thread

                    # self.currentTime = indigo.server.getTime()  # TODO: Not needed?

                    # Check if monitoring / debug options have changed and if so set accordingly
                    if self.globals['debug']['previousTrvHandler'] != self.globals['debug']['trvHandler']:
                        self.globals['debug']['previousTrvHandler'] = self.globals['debug']['trvHandler']
                        self.trvHandlerLogger.setLevel(self.globals['debug']['trvHandler'])

                    if trvCommandDevId is not None:
                        self.trvHandlerLogger.debug(f'\nTRVHANDLER: \'{indigo.devices[trvCommandDevId].name}\' DEQUEUED COMMAND \'{CMD_TRANSLATION[trvCommand]}\'')
                    else:
                        self.trvHandlerLogger.debug(f'\nTRVHANDLER: DEQUEUED COMMAND \'{CMD_TRANSLATION[trvCommand]}\'')

                    if trvCommand == CMD_ACTION_POLL:
                        self.pollSpiritActioned(trvCommandDevId)
                        continue

                    if trvCommand == CMD_TRIGGER_POLL:
                        self.pollSpiritTriggered(trvCommandDevId)
                        continue

                    if trvCommand in (CMD_UPDATE_TRV_CONTROLLER_STATES, CMD_UPDATE_TRV_STATES, CMD_UPDATE_REMOTE_STATES, CMD_UPDATE_VALVE_STATES):
                        updateList = trvCommandPackage[0]
                        self.updateDeviceStates(trvCommandDevId, trvCommand, updateList, trvQueueSequence)
                        continue

                    if trvCommand == CMD_CONTROL_HEATING_SOURCE:
                        heatingDevId = trvCommandPackage[0]
                        heatingVarId = trvCommandPackage[1]
                        self.controlHeatingSource(trvCommandDevId, heatingDevId, heatingVarId)  # Device IDs: TRV Controller ID, Device Heating Source ID and Variable Heating Source ID
                        continue

                    if trvCommand == CMD_KEEP_HEAT_SOURCE_CONTROLLER_ALIVE:
                        heatingDevId = trvCommandPackage[0]
                        self.keepHeatSourceControllerAlive(heatingDevId)  # Device ID is for Heating Source device
                        continue

                    if trvCommand == CMD_CONTROL_TRV:
                        self.controlTrv(trvCommandDevId)  # Device ID is for TRV Controller
                        continue

                    if trvCommand == CMD_DELAY_COMMAND:
                        trvDelayedCommand = trvCommandPackage[0]
                        trvDelayedSeconds = trvCommandPackage[1]
                        trvDelayedCommandPackage = trvCommandPackage[2]
                        self.delayCommand(trvDelayedCommand, trvCommandDevId, trvDelayedSeconds, trvDelayedCommandPackage)
                        continue

                    if trvCommand == CMD_PROCESS_HEATING_SCHEDULE:
                        self.processHeatingSchedule(trvCommandDevId)
                        continue

                    if trvCommand == CMD_RESTATE_SCHEDULES:
                        self.restateSchedules()
                        continue

                    if trvCommand == CMD_RESET_SCHEDULE_TO_DEVICE_DEFAULTS:
                        self.resetScheduleToDeviceDefaults(trvCommandDevId)
                        continue

                    if trvCommand == CMD_BOOST:
                        boostMode = trvCommandPackage[0]
                        boostDeltaT = trvCommandPackage[1]
                        boostSetpoint = trvCommandPackage[2]
                        boostMinutes = trvCommandPackage[3]
                        self.processBoost(trvCommandDevId, boostMode, boostDeltaT, boostSetpoint, boostMinutes)
                        continue

                    if trvCommand == CMD_BOOST_CANCEL:
                        invokeProcessHeatingSchedule = trvCommandPackage[0]  # True or False
                        self.boostCancelTriggered(trvCommandDevId, invokeProcessHeatingSchedule)
                        continue

                    if trvCommand == CMD_ADVANCE:
                        advanceType = trvCommandPackage[0]
                        self.processAdvance(trvCommandDevId, advanceType)
                        continue

                    if trvCommand == CMD_ADVANCE_CANCEL:
                        invokeProcessHeatingSchedule = trvCommandPackage[0]
                        self.processAdvanceCancel(trvCommandDevId, invokeProcessHeatingSchedule)
                        continue

                    if trvCommand == CMD_EXTEND:
                        extendIncrementMinutes = trvCommandPackage[0]
                        extendMaximumMinutes = trvCommandPackage[1]
                        self.processExtend(trvCommandDevId, extendIncrementMinutes, extendMaximumMinutes)
                        continue

                    if trvCommand == CMD_EXTEND_CANCEL:
                        invokeProcessHeatingSchedule = trvCommandPackage[0]
                        self.processExtendCancel(trvCommandDevId, invokeProcessHeatingSchedule)
                        continue

                    if trvCommand == CMD_UPDATE_CSV_FILE:
                        stateName = trvCommandPackage[0]
                        updateValue = trvCommandPackage[1]
                        self.updateCsvFile(trvCommandDevId, stateName, updateValue)
                        continue

                    if trvCommand == CMD_UPDATE_ALL_CSV_FILES:
                        self.updateAllCsvFiles(trvCommandDevId)
                        continue

                    if trvCommand == CMD_UPDATE_ALL_CSV_FILES_VIA_POSTGRESQL:
                        overrideDefaultRetentionHours = trvCommandPackage[0]
                        overrideCsvFilePrefix = trvCommandPackage[1]
                        self.updateAllCsvFilesViaPostgreSQL(trvCommandDevId, overrideDefaultRetentionHours, overrideCsvFilePrefix)
                        continue

                    self.trvHandlerLogger.error(f'TRVHandler: \'{CMD_TRANSLATION[trvCommand]}\' command cannot be processed')

                except queue.Empty:
                    pass
                except Exception as exception_error:
                    self.exception_handler(exception_error, True)  # Log error and display failing statement

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

        self.trvHandlerLogger.debug('TRV Handler Thread ended.')

    def controlHeatingSource(self, trvCtlrDevId, heatingId, heatingVarId):  # noqa - trvCtlrDevId not used

        # Determine if heating should be started / ended

        if heatingId == 0 and heatingVarId == 0:
            return

        self.globals['lock'].acquire()

        try:
            if heatingId != 0:
                if len(self.globals['heaterDevices'][heatingId]['thermostatsCallingForHeat']) == 0:
                    callingForHeatUi = 'None'
                else:
                    callingForHeatUi = '\n'
                    for callingForHeatTrvCtlrDevId in self.globals['heaterDevices'][heatingId]['thermostatsCallingForHeat']:
                        callingForHeatUi = callingForHeatUi + f'  > {indigo.devices[callingForHeatTrvCtlrDevId].name}\n'

                self.trvHandlerLogger.debug(
                    f'Control Heating Source: {len(self.globals["heaterDevices"][heatingId]["thermostatsCallingForHeat"])} Thermostats calling for heat from Device \'{indigo.devices[heatingId].name}\': {callingForHeatUi}')
                if len(self.globals['heaterDevices'][heatingId]['thermostatsCallingForHeat']) > 0:
                    # if there are thermostats calling for heat, the heating needs to be 'on'
                    # indigo.variable.updateValue(self.variableId, value="true")  # Variable indicator to show that heating is being requested
                    if self.globals['heaterDevices'][heatingId]['onState'] != HEAT_SOURCE_ON:
                        self.globals['heaterDevices'][heatingId]['onState'] = HEAT_SOURCE_ON
                        if self.globals['heaterDevices'][heatingId]['heaterControlType'] == HEAT_SOURCE_CONTROL_HVAC:
                            if indigo.devices[heatingId].states['hvacOperationMode'] != HVAC_HEAT:
                                indigo.thermostat.setHvacMode(heatingId, value=HVAC_HEAT)  # Turn heating 'on'
                        elif self.globals['heaterDevices'][heatingId]['heaterControlType'] == HEAT_SOURCE_CONTROL_RELAY:
                            if not indigo.devices[heatingId].onState:
                                indigo.device.turnOn(heatingId)  # Turn heating 'on'
                        else:
                            pass  # ERROR SITUATION
                else:
                    # if no thermostats are calling for heat, then the heating needs to be 'off'
                    # indigo.variable.updateValue(self.variableId, value="false")  # Variable indicator to show that heating is NOT being requested
                    if self.globals['heaterDevices'][heatingId]['onState'] != HEAT_SOURCE_OFF:
                        self.globals['heaterDevices'][heatingId]['onState'] = HEAT_SOURCE_OFF
                        if self.globals['heaterDevices'][heatingId]['heaterControlType'] == HEAT_SOURCE_CONTROL_HVAC:
                            if indigo.devices[heatingId].states['hvacOperationMode'] != HVAC_OFF:
                                indigo.thermostat.setHvacMode(heatingId, value=HVAC_OFF)  # Turn heating 'off'
                        elif self.globals['heaterDevices'][heatingId]['heaterControlType'] == HEAT_SOURCE_CONTROL_RELAY:
                            if indigo.devices[heatingId].onState:
                                indigo.device.turnOff(heatingId)  # Turn heating 'off'
                        else:
                            pass  # ERROR SITUATION

            if heatingVarId != 0:
                if len(self.globals['heaterVariables'][heatingVarId]['thermostatsCallingForHeat']) == 0:
                    callingForHeatUi = 'None'
                else:
                    callingForHeatUi = '\n'
                    for callingForHeatTrvCtlrDevId in self.globals['heaterVariables'][heatingVarId]['thermostatsCallingForHeat']:
                        callingForHeatUi = callingForHeatUi + f'  > {indigo.devices[callingForHeatTrvCtlrDevId].name}\n'

                self.trvHandlerLogger.debug(f'Control Heating Source: Thermostats calling for heat from Variable \'{indigo.variables[heatingVarId].name}\': {callingForHeatUi}')
                if len(self.globals['heaterVariables'][heatingVarId]['thermostatsCallingForHeat']) > 0:
                    # if there are thermostats calling for heat, the heating needs to be 'on'
                    indigo.variable.updateValue(heatingVarId, value="true")  # Variable indicator to show that heating is being requested
                else:
                    # if no thermostats are calling for heat, then the heating needs to be 'off'
                    indigo.variable.updateValue(heatingVarId, value="false")  # Variable indicator to show that heating is NOT being requested

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement
        finally:
            self.globals['lock'].release()

    def controlTrv(self, trvCtlrDevId):

        try:
            # Control the thermostat that is controlled by this TRV Controller (trvCtlrDevId)

            if trvCtlrDevId not in self.globals['trvc'] or 'deviceStarted' not in self.globals['trvc'][trvCtlrDevId] or not self.globals['trvc'][trvCtlrDevId]['deviceStarted']:
                self.trvHandlerLogger.debug(f'controlTrv: \'{indigo.devices[trvCtlrDevId].name}\' startup not yet completed')
                return

            trvDevId = self.globals['trvc'][trvCtlrDevId]['trvDevId']
            trvDev = indigo.devices[trvDevId]
            remoteDevId = self.globals['trvc'][trvCtlrDevId]['remoteDevId']

            self.trvHandlerLogger.debug(
                f'controlTrv: \'{indigo.devices[trvCtlrDevId].name}\' is set to Controller Mode \'{CONTROLLER_MODE_TRANSLATION[self.globals["trvc"][trvCtlrDevId]["controllerMode"]]}\'')
            self.trvHandlerLogger.debug(
                f'controlTrv: \'{indigo.devices[trvCtlrDevId].name}\' internal states [1] are: controllerMode = {self.globals["trvc"][trvCtlrDevId]["controllerMode"]}, setpointHeat = {self.globals["trvc"][trvCtlrDevId]["setpointHeat"]}, setPointTrv =  {self.globals["trvc"][trvCtlrDevId]["setpointHeatTrv"]}')

            if not self.globals['trvc'][trvCtlrDevId]['deviceStarted'] or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_INITIALISATION:  # Return if still in initialisation
                return

            if self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_REMOTE_HARDWARE or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_REMOTE_UI:
                self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote'])
            elif self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_TRV_HARDWARE or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_TRV_UI:
                self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])
            else:
                # self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_Auto or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_TRV_UI:
                # Must be one of: CONTROLLER_MODE_AUTO / CONTROLLER_MODE_UI
                pass

            self.trvHandlerLogger.debug(
                f'controlTrv: \'{indigo.devices[trvCtlrDevId].name}\' internal states [2] are: controllerMode = {self.globals["trvc"][trvCtlrDevId]["controllerMode"]}, setpointHeat = {self.globals["trvc"][trvCtlrDevId]["setpointHeat"]}, setPointTrv =  {self.globals["trvc"][trvCtlrDevId]["setpointHeatTrv"]}')

            # Set the Remote Thermostat setpoint if not invoked by remote, and it exists and, setpoint adjustment is enabled

            if self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_AUTO or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_UI or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_TRV_HARDWARE or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_TRV_UI:
                if remoteDevId != 0 and self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl']:
                    if float(indigo.devices[remoteDevId].heatSetpoint) != float(self.globals['trvc'][trvCtlrDevId]['setpointHeat']):
                        self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeat'])
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointFlag'] = True
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointSequence'] += 1
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointValue'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])
                        indigo.thermostat.setHeatSetpoint(remoteDevId, value=float(self.globals['trvc'][trvCtlrDevId]['setpointHeat']))  # Set Remote Heat Setpoint to Target Temperature
                        self.trvHandlerLogger.debug(
                            f'controlTrv: Adjusting Remote Setpoint Heat from {float(indigo.devices[remoteDevId].heatSetpoint)} to Target Temperature of {float(self.globals["trvc"][trvCtlrDevId]["setpointHeat"])}')
                        indigo.devices[trvCtlrDevId].updateStateOnServer(key='setpointHeatRemote', value=float(self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote']))

            hvacFullPower = False
            if trvDev.model == 'Thermostat (Spirit)' and 'zwaveHvacOperationModeID' in trvDev.states and trvDev.states['zwaveHvacOperationModeID'] == HVAC_FULL_POWER:
                hvacFullPower = True

            self.trvHandlerLogger.debug(f'controlTrv: \'{indigo.devices[trvCtlrDevId].name}\' internal states [3] are: HVAC_FULL_POWER = {hvacFullPower}')

            if (float(self.globals['trvc'][trvCtlrDevId]['setpointHeat']) <= float(self.globals['trvc'][trvCtlrDevId]['temperature'])) and not hvacFullPower:

                # TRV should be turned off as its temperature is greater than or equal to the target Temperature

                self.controlTrvHeatingOff(trvCtlrDevId)  # TRV no longer calling for heat
                self.controlHeatingSource(trvCtlrDevId, self.globals['trvc'][trvCtlrDevId]['heatingId'], self.globals['trvc'][trvCtlrDevId]['heatingVarId'])

                if (self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_AUTO or
                        self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_UI or
                        self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_REMOTE_HARDWARE or
                        self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_REMOTE_UI):

                    if float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']) != float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum']):
                        self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum'])
                    if indigo.devices[trvDevId].heatSetpoint != float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']):
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointFlag'] = True
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointSequence'] += 1
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointValue'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])
                        indigo.thermostat.setHeatSetpoint(trvDevId, value=float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']))
                        self.trvHandlerLogger.debug(
                            f'controlTrv: Turning OFF and adjusting TRV Setpoint Heat to \'{float(self.globals["trvc"][trvCtlrDevId]["setpointHeatTrv"])}\'. Z-Wave Pending = {self.globals["trvc"][trvCtlrDevId]["zwavePendingTrvSetpointFlag"]}, Setpoint = \'{self.globals["trvc"][trvCtlrDevId]["zwavePendingTrvSetpointValue"]}\', Sequence = \'{self.globals["trvc"][trvCtlrDevId]["zwavePendingTrvSetpointSequence"]}\'.')

                        indigo.devices[trvCtlrDevId].updateStateOnServer(key='setpointHeatTrv', value=float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']))

                        if self.globals['trvc'][trvCtlrDevId]['valveDevId'] != 0:  # e.g. EUROTronic Spirit Thermostat
                            if self.globals['trvc'][trvCtlrDevId]['advancedOption'] == ADVANCED_OPTION_FIRMWARE_WORKAROUND:
                                self.trvHandlerLogger.debug(f'controlTrv: >>>>>> \'{indigo.devices[trvDevId].name}\' SUPPORTS VALVE CONTROL - CLOSING VALVE <<<<<<<<<')
                                zwaveRawCommandSequence = list()
                                zwaveRawCommandSequence.append((1, 0, [], 'Timer Initialisation'))
                                zwaveRawCommandSequence.append((1, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x0F], 'Thermostat Mode Control - Boost'))
                                zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x00], 'Thermostat Mode Control - Off'))
                                zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x01], 'Thermostat Mode Control - Heat'))
                                if self.globals['trvc'][trvCtlrDevId]['enableTrvOnOff']:
                                    zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x00], 'Thermostat Mode Control - Off'))
                                self.controlTrvSpiritValveCommandsQueued(trvCtlrDevId, zwaveRawCommandSequence)

                            elif self.globals['trvc'][trvCtlrDevId]['advancedOption'] == ADVANCED_OPTION_VALVE_ASSISTANCE:
                                self.trvHandlerLogger.debug(f'controlTrv: >>>>>> \'{indigo.devices[trvDevId].name}\' SUPPORTS VALVE CONTROL - CLOSING VALVE <<<<<<<<<')
                                zwaveRawCommandSequence = list()
                                zwaveRawCommandSequence.append((1, 0, [], 'Timer Initialisation'))
                                zwaveRawCommandSequence.append((1, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x1F], 'Thermostat Mode Control - Valve Control'))
                                zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x01, 0x00], 'Switch Multilevel - Valve = 0%'))
                                zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x01, 0x00], 'Switch Multilevel - Valve = 0%'))
                                zwaveRawCommandSequence.append((2, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x02], 'Switch Multilevel - Status Update'))
                                zwaveRawCommandSequence.append((2, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x02], 'Switch Multilevel - Status Update'))
                                zwaveRawCommandSequence.append((1, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x01], 'Thermostat Mode Control - Heat'))
                                if self.globals['trvc'][trvCtlrDevId]['enableTrvOnOff']:
                                    zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x00], 'Thermostat Mode Control - Off'))
                                self.controlTrvSpiritValveCommandsQueued(trvCtlrDevId, zwaveRawCommandSequence)

                        if self.globals['trvc'][trvCtlrDevId]['enableTrvOnOff']:
                            indigo.thermostat.setHvacMode(trvDevId, value=HVAC_OFF)

            if float(self.globals['trvc'][trvCtlrDevId]['setpointHeat']) > float(self.globals['trvc'][trvCtlrDevId]['temperature']) or hvacFullPower:

                # TRV should be turned on as its temperature is less than target Temperature

                self.controlTrvHeatingOn(trvCtlrDevId)  # TRV calling for heat
                self.controlHeatingSource(trvCtlrDevId, self.globals['trvc'][trvCtlrDevId]['heatingId'], self.globals['trvc'][trvCtlrDevId]['heatingVarId'])

                if self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_AUTO or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_UI or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_REMOTE_HARDWARE or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_REMOTE_UI:

                    deltaMax = 0.0
                    if remoteDevId != 0:
                        deltaMax = float(self.globals['trvc'][trvCtlrDevId]['remoteDeltaMax'])

                    targetHeatSetpoint = float(float(self.globals['trvc'][trvCtlrDevId]['setpointHeat']) + float(deltaMax))  # + deltaMax either remoteDeltaMax, if TRV controlled by remote thermostat or zero
                    if targetHeatSetpoint > float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum']):
                        targetHeatSetpoint = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum'])

                    if float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']) != targetHeatSetpoint:
                        self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'] = targetHeatSetpoint

                    if indigo.devices[trvDevId].heatSetpoint != float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']):
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointFlag'] = True
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointSequence'] += 1
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointValue'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])
                        indigo.thermostat.setHeatSetpoint(trvDevId, value=float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']))
                        self.trvHandlerLogger.debug(
                            f'controlTrv: Turning ON and adjusting TRV Setpoint Heat to \'{float(self.globals["trvc"][trvCtlrDevId]["setpointHeatTrv"])}\'. Z-Wave Pending = {self.globals["trvc"][trvCtlrDevId]["zwavePendingTrvSetpointFlag"]}, Setpoint = \'{self.globals["trvc"][trvCtlrDevId]["zwavePendingTrvSetpointValue"]}\', Sequence = \'{self.globals["trvc"][trvCtlrDevId]["zwavePendingTrvSetpointSequence"]}\'.')

                        indigo.devices[trvCtlrDevId].updateStateOnServer(key='setpointHeatTrv', value=float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']))

                        if self.globals['trvc'][trvCtlrDevId]['enableTrvOnOff'] or self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'] == HVAC_OFF:
                            indigo.thermostat.setHvacMode(trvDevId, value=HVAC_HEAT)

                        if self.globals['trvc'][trvCtlrDevId]['valveDevId'] != 0:  # e.g. EUROTronic Spirit Thermostat special logic
                            if self.globals['trvc'][trvCtlrDevId]['advancedOption'] == ADVANCED_OPTION_VALVE_ASSISTANCE:
                                self.trvHandlerLogger.debug(f'controlTrv: >>>>>> \'{indigo.devices[trvDevId].name}\' SUPPORTS VALVE CONTROL - OPENING VALVE <<<<<<<<<')

                                zwaveRawCommandSequence = list()
                                zwaveRawCommandSequence.append((1, 0, [], 'Timer Initialisation'))
                                zwaveRawCommandSequence.append((1, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x1F], 'Thermostat Mode Control - Valve Control'))
                                zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x01, 0x63], 'Switch Multilevel - Valve = 100%'))
                                zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x01, 0x63], 'Switch Multilevel - Valve = 100%'))
                                zwaveRawCommandSequence.append((2, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x02], 'Switch Multilevel - Status Update'))
                                zwaveRawCommandSequence.append((2, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x02], 'Switch Multilevel - Status Update'))
                                zwaveRawCommandSequence.append((1, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x01], 'Thermostat Mode Control - Heat'))
                                # zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x00], 'Thermostat Mode Control - Off'))
                                # zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x0F], 'Thermostat Mode Control - boost'))
                                # zwaveRawCommandSequence.append((1, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x01], 'Thermostat Mode Control - Heat'))
                                self.controlTrvSpiritValveCommandsQueued(trvCtlrDevId, zwaveRawCommandSequence)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def controlTrvHeatingOff(self, trvCtlrDevId):

        try:
            try:
                self.globals['lock'].acquire()
                if self.globals['trvc'][trvCtlrDevId]['heatingId'] > 0:
                    self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['thermostatsCallingForHeat'].discard(trvCtlrDevId)  # Remove TRV Controller from the SET thermostatsCallingForHeat
                if self.globals['trvc'][trvCtlrDevId]['heatingVarId'] > 0:
                    self.globals['heaterVariables'][self.globals['trvc'][trvCtlrDevId]['heatingVarId']]['thermostatsCallingForHeat'].discard(trvCtlrDevId)  # Remove TRV Controller from the SET thermostatsCallingForHeat
            except Exception as exception_error:
                self.exception_handler(exception_error, True)  # Log error and display failing statement
            finally:
                self.globals['lock'].release()

                indigo.devices[trvCtlrDevId].updateStateOnServer(key='hvacHeaterIsOn', value=False)
                indigo.devices[trvCtlrDevId].updateStateImageOnServer(indigo.kStateImageSel.HvacHeatMode)  # HvacOff - HvacHeatMode - HvacHeating - HvacAutoMode

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def controlTrvHeatingOn(self, trvCtlrDevId):

        try:
            try:
                self.globals['lock'].acquire()
                if self.globals['trvc'][trvCtlrDevId]['heatingId'] > 0:
                    self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['thermostatsCallingForHeat'].add(trvCtlrDevId)  # Add TRV Controller to the SET thermostatsCallingForHeat
                if self.globals['trvc'][trvCtlrDevId]['heatingVarId'] > 0:
                    self.globals['heaterVariables'][self.globals['trvc'][trvCtlrDevId]['heatingVarId']]['thermostatsCallingForHeat'].add(trvCtlrDevId)  # Add TRV Controller to the SET thermostatsCallingForHeat
                self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'] = HVAC_HEAT
            except Exception as exception_error:
                self.exception_handler(exception_error, True)  # Log error and display failing statement
            finally:
                self.globals['lock'].release()

                indigo.devices[trvCtlrDevId].updateStateOnServer(key='hvacHeaterIsOn', value=True)
                indigo.devices[trvCtlrDevId].updateStateImageOnServer(indigo.kStateImageSel.HvacHeatMode)  # HvacOff - HvacHeatMode - HvacHeating - HvacAutoMode

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def controlTrvSpiritTriggered(self, trvCtlrDevId, zwaveRawCommandSequence):

        try:
            spiritValveId = self.globals['trvc'][trvCtlrDevId]['valveDevId']
            spiritValveDev = indigo.devices[spiritValveId]

            self.trvHandlerLogger.debug(f'controlTrvSpiritTriggered for device \'{spiritValveDev.name}\' with Command Sequence [{len(zwaveRawCommandSequence)}]:\n\n{zwaveRawCommandSequence}\n')

            seconds, targetDeviceId, zwaveRawCommandString, zwaveRawCommandDescription  = zwaveRawCommandSequence.pop(0)  # FIFO List
            if len(zwaveRawCommandString) > 0:
                indigo.zwave.sendRaw(device=indigo.devices[targetDeviceId], cmdBytes=zwaveRawCommandString, sendMode=1)
                self.trvHandlerLogger.debug(f'>>>>>> ZWave Raw Command for device \'{indigo.devices[targetDeviceId].name}\' = {zwaveRawCommandDescription}')
                if zwaveRawCommandString == [0x40, 0x01, 0x00]:
                    indigo.thermostat.setHvacMode(targetDeviceId, value=HVAC_OFF)
                elif zwaveRawCommandString == [0x40, 0x01, 0x01]:
                    indigo.thermostat.setHvacMode(targetDeviceId, value=HVAC_HEAT)
            if len(zwaveRawCommandSequence) > 0:
                delaySeconds = zwaveRawCommandSequence[0][0]
                self.globals['timers']['SpiritValveCommands'][trvCtlrDevId] = threading.Timer(float(delaySeconds), self.controlTrvSpiritTriggered, [trvCtlrDevId, zwaveRawCommandSequence])
                self.globals['timers']['SpiritValveCommands'][trvCtlrDevId].setDaemon(True)
                self.globals['timers']['SpiritValveCommands'][trvCtlrDevId].start()

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def controlTrvSpiritValveCommandsQueued(self, trvCtlrDevId, zwaveRawCommandSequence):

        try:
            spiritValveId = self.globals['trvc'][trvCtlrDevId]['valveDevId']
            spiritValveDev = indigo.devices[spiritValveId]
            self.trvHandlerLogger.debug('controlTrvSpiritQueued')

            if trvCtlrDevId in self.globals['timers']['SpiritValveCommands']:
                self.globals['timers']['SpiritValveCommands'][trvCtlrDevId].cancel()
                self.trvHandlerLogger.debug(f'controlTrvSpiritValveCommandsQueued timer cancelled for device \'{spiritValveDev.name}\' with now cancelled Command Sequence:\n{zwaveRawCommandSequence}')

            self.controlTrvSpiritTriggered(trvCtlrDevId, zwaveRawCommandSequence)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def convertUnicode(self, unicodeInput):
        if isinstance(unicodeInput, dict):
            return dict(
                [(self.convertUnicode(key), self.convertUnicode(value)) for key, value in unicodeInput.items()])
        elif isinstance(unicodeInput, list):
            return [self.convertUnicode(element) for element in unicodeInput]
        elif isinstance(unicodeInput, unicode):
            return unicodeInput.encode('utf-8')
        else:
            return unicodeInput

    def delayCommand(self, trvDelayedCommand, trvCtlrDevId, trvDelayedSeconds, trvDelayedCommandPackage):

        try:
            self.globals['timers']['command'][trvCtlrDevId] = threading.Timer(float(trvDelayedSeconds), self.delayCommandTimerTriggered, [trvDelayedCommand, trvCtlrDevId, trvDelayedCommandPackage])  # 3,300 seconds = 55 minutes :)
            self.globals['timers']['command'][trvCtlrDevId].setDaemon(True)
            self.globals['timers']['command'][trvCtlrDevId].start()

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def delayCommandTimerTriggered(self, trvDelayedCommand, trvCtlrDevId, trvDelayedCommandPackage):

        try:
            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, trvDelayedCommand, trvCtlrDevId, trvDelayedCommandPackage])

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    # noinspection PyUnusedLocal
    def keepHeatSourceControllerAlive(self, heatingId):

        try:
            self.trvHandlerLogger.debug(f'\'keepHeatSourceControllerAlive\' invoked for:  {indigo.devices[heatingId].model} ...')

            # Only needed for SSR302 / SSR303 - needs updating every 55 minutes
            if indigo.devices[heatingId].model == "1 Channel Boiler Actuator (SSR303 / ASR-ZW)" or indigo.devices[heatingId].model == "2 Channel Boiler Actuator (SSR302)":
                self.trvHandlerLogger.debug(
                    f'\'keepHeatSourceControllerAlive\' invoked for:  {indigo.devices[heatingId].name} - Number of TRVs calling for heat = {len(self.globals["heaterDevices"][heatingId]["thermostatsCallingForHeat"])}')
                self.globals['lock'].acquire()
                try:
                    # if there are thermostats calling for heat, the heating needs to be 'on'
                    if len(self.globals['heaterDevices'][heatingId]['thermostatsCallingForHeat']) > 0:
                        indigo.thermostat.setHvacMode(heatingId, value=HVAC_HEAT)  # remind Heat Source Controller to stay 'on'
                        self.trvHandlerLogger.debug(f'\'keepHeatSourceControllerAlive\':  Reminding Heat Source Controller {indigo.devices[heatingId].name} to stay \'ON\'')
                    else:
                        indigo.thermostat.setHvacMode(heatingId, value=HVAC_OFF)  # remind Heat Source Controller to stay 'off'
                        self.trvHandlerLogger.debug(f'\'keepHeatSourceControllerAlive\':  Reminding Heat Source Controller {indigo.devices[heatingId].name} to stay \'OFF\'')
                except Exception as exception_error:
                    self.exception_handler(exception_error, True)  # Log error and display failing statement
                finally:
                    self.globals['lock'].release()

                self.globals['timers']['heaters'][heatingId] = threading.Timer(3300.0, self.keepHeatSourceControllerAliveTimerTriggered, [heatingId])  # 3,300 seconds = 55 minutes :)
                self.globals['timers']['heaters'][heatingId].setDaemon(True)
                self.globals['timers']['heaters'][heatingId].start()
            else:
                self.trvHandlerLogger.debug(f'... {indigo.devices[heatingId].model} doesn\'t need to be kept alive!')

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def keepHeatSourceControllerAliveTimerTriggered(self, heatingId):

        try:
            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_KEEP_HEAT_SOURCE_CONTROLLER_ALIVE, None, [heatingId, ]])

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def pollSpiritActioned(self, trvCtlrDevId):

        try:
            pollingSeconds = float(self.globals['trvc'][trvCtlrDevId]['pollingSeconds'])

            trvDevId = self.globals['trvc'][trvCtlrDevId]['trvDevId']
            valveDevId = self.globals['trvc'][trvCtlrDevId]['valveDevId']

            self.trvHandlerLogger.debug(f'pollSpiritActioned: Polling \'{indigo.devices[trvDevId].name}\' Spirit Thermostat every {int(pollingSeconds)} seconds.')

            indigo.device.statusRequest(trvDevId)  # Request Spirit Thermostat status

            if valveDevId != 0:
                indigo.device.statusRequest(valveDevId)  # Request Spirit Valve status

            self.globals['timers']['SpiritPolling'][trvCtlrDevId] = threading.Timer(pollingSeconds, self.pollSpiritTriggered, [trvCtlrDevId])  # Initiate next poll
            self.globals['timers']['SpiritPolling'][trvCtlrDevId].setDaemon(True)
            self.globals['timers']['SpiritPolling'][trvCtlrDevId].start()

            self.trvHandlerLogger.debug(f'pollSpiritActioned: Polling \'{indigo.devices[trvDevId].name}\' Spirit Thermostat every {int(pollingSeconds)} seconds.')

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def pollSpiritTriggered(self, trvCtlrDevId):

        try:
            if trvCtlrDevId in self.globals['timers']['SpiritPolling']:
                self.globals['timers']['SpiritPolling'][trvCtlrDevId].cancel()
                del self.globals['timers']['SpiritPolling'][trvCtlrDevId]

            if self.globals['config']['delayQueueSeconds'] == 0:
                self.pollSpiritActioned(trvCtlrDevId)
            else:
                # self.globals['trvc'][trvCtlrDevId]['pollingSequence'] += 1
                # self.globals['queues']['delayHandler'].put([CMD_ACTION_POLL, trvCtlrDevId, self.globals['trvc'][trvCtlrDevId]['pollingSequence']])
                self.globals['queues']['delayHandler'].put([CMD_ACTION_POLL, trvCtlrDevId])

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def processAdvance(self, trvCtlrDevId, advanceType):
        try:
            self.trvHandlerLogger.debug(
                f'processAdvance [0]: Type = [{ADVANCE_TRANSLATION[advanceType]}]\nRunning:\n{self.globals["schedules"][trvCtlrDevId]["running"]}\n\nDynamic:\n{self.globals["schedules"][trvCtlrDevId]["dynamic"]}\n\n')
            
            if trvCtlrDevId in self.globals['timers']['heatingSchedules']:  # Cancel any existing heating schedule timer
                self.globals['timers']['heatingSchedules'][trvCtlrDevId].cancel()

            if trvCtlrDevId in self.globals['timers']['advanceCancel']:  # Cancel any existing advance cancel timer
                self.globals['timers']['advanceCancel'][trvCtlrDevId].cancel()

            self.processBoostCancel(trvCtlrDevId, False)
            self.processExtendCancel(trvCtlrDevId, False)

            scheduleList = self.globals['schedules'][trvCtlrDevId]['dynamic'].copy()

            trvcDev = indigo.devices[trvCtlrDevId]

            initialiseHeatingScheduleLog = f'\n\n{"|" * 80}'
            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n||  Device: {indigo.devices[trvCtlrDevId].name}\n||  Method: processAdvance [BEFORE]'
            for key, value in scheduleList.items():
                # scheduleTime = int(key)
                scheduleTimeUi = f'{value[0]}'
                scheduleSetpoint = float(value[1])
                scheduleId = int(value[2])
                # scheduleActive = bool(value[3])
                initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n||  Time = {scheduleTimeUi}, Setpoint = {scheduleSetpoint}, Id = {scheduleId}'
            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n||  ScheduleList Length = {len(scheduleList)}, ScheduleList Type = {type(scheduleList)}'
            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n{"||" * 80}\n\n'
            self.trvHandlerLogger.debug(initialiseHeatingScheduleLog)

            ct = int(datetime.datetime.now().strftime('%H%M%S'))

            scheduleKeyPrevious = 0
            scheduleKeyNext = 0

            for key, value in scheduleList.items():
                if key <= ct:
                    scheduleKeyPrevious = key
                else:
                    if key == 240000 and scheduleKeyNext == 0:
                        scheduleKeyNext = 240000
                    else:
                        if advanceType == ADVANCE_NEXT:
                            if scheduleKeyNext == 0:
                                scheduleKeyNext = key
                        elif advanceType == ADVANCE_NEXT_ON:
                            if scheduleKeyNext == 0 and value[3]:  # Schedule Active | ON:
                                scheduleKeyNext = key
                        elif advanceType == ADVANCE_NEXT_OFF:
                            if scheduleKeyNext == 0 and not value[3]:  # Schedule Not Active | OFF:
                                scheduleKeyNext = key

            scheduleListAdvancedCheck = scheduleList.copy()

            for key, value in scheduleListAdvancedCheck.items():
                if ct < key < scheduleKeyNext:
                    del scheduleList[key]

            initialiseHeatingScheduleLog = f'\n\n{"|" * 80}'
            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n||  Device: {indigo.devices[trvCtlrDevId].name}\n||  Method: processAdvance [AFTER]'
            for key, value in scheduleList.items():
                # scheduleTime = int(key)
                scheduleTimeUi = f'{value[0]}'
                scheduleSetpoint = float(value[1])
                scheduleId = int(value[2])
                # scheduleActive = bool(value[3])
                initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n||  Time = {scheduleTimeUi}, Setpoint = {scheduleSetpoint}, Id = {scheduleId}'
            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n||\n|| Type={ADVANCE_TRANSLATION[advanceType]}, CT={ct}, Prev={scheduleKeyPrevious}, Next={scheduleKeyNext}'

            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n||  ScheduleList Length = {len(scheduleList)}, ScheduleList Type = {type(scheduleList)}'
            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n{"||" * 80}\n\n'
            self.trvHandlerLogger.debug(initialiseHeatingScheduleLog)

            # previousSchedule = 0
            nextSchedule = 0

            for key, value in scheduleList.items():
                if key <= ct:
                    pass
                    # previousSchedule = key
                else:
                    if key == 240000 and nextSchedule == 0:
                        nextSchedule = 240000
                    else:
                        if nextSchedule == 0:
                            nextSchedule = key

            if nextSchedule == 240000:
                self.trvHandlerLogger.info(f'TRV Controller \'{trvcDev.name}\' - No further schedule to \'Advance\' to - Advance not actioned!')
                return

            ctTemp = f'0{ct}'[-6:]  # e.g 91045 > 091045
            ctUi = f'{ctTemp[0:2]}:{ctTemp[2:4]}'  # e.g. 09:10

            schedule = scheduleList[nextSchedule]
            scheduleTimeUi = f'{schedule[0]}'
            scheduleSetpoint = float(schedule[1])
            scheduleId = int(schedule[2])
            scheduleActive = bool(schedule[3])
            scheduleActiveUi = 'Start' if scheduleActive else 'End'

            del scheduleList[nextSchedule]

            scheduleList[ct] = (ctUi, scheduleSetpoint, scheduleId, scheduleActive)

            self.globals['schedules'][trvCtlrDevId]['dynamic'] = collections.OrderedDict(sorted(scheduleList.items())).copy()

            self.trvHandlerLogger.debug(f'processAdvance [2]:\nRunning:\n{self.globals["schedules"][trvCtlrDevId]["running"]}\n\nDynamic:\n{self.globals["schedules"][trvCtlrDevId]["dynamic"]}\n\n')

            self.globals['trvc'][trvCtlrDevId]['advanceActive'] = True
            self.globals['trvc'][trvCtlrDevId]['advanceStatusUi'] = f'Advanced to S{scheduleId} \'{scheduleActiveUi} at {scheduleTimeUi}\' at {ctUi}'
            self.globals['trvc'][trvCtlrDevId]['advanceActivatedTime'] = ctUi
            self.globals['trvc'][trvCtlrDevId]['advanceToScheduleTime'] = scheduleTimeUi

            keyValueList = [
                    {'key': 'advanceActive', 'value': self.globals['trvc'][trvCtlrDevId]['advanceActive']},
                    {'key': 'advanceStatusUi', 'value': self.globals['trvc'][trvCtlrDevId]['advanceStatusUi']},
                    {'key': 'advanceActivatedTime', 'value': self.globals['trvc'][trvCtlrDevId]['advanceActivatedTime']},
                    {'key': 'advanceToScheduleTime', 'value': self.globals['trvc'][trvCtlrDevId]['advanceToScheduleTime']}
                ]
            indigo.devices[trvCtlrDevId].updateStatesOnServer(keyValueList)

            self.trvHandlerLogger.info(f'TRV Controller \'{trvcDev.name}\' - {self.globals["trvc"][trvCtlrDevId]["advanceStatusUi"]}')

            # Set Timer to cancel advance when next schedule time reached
            secondsToNextSchedule, calcSecondsLog = calcSeconds(nextSchedule, ct)

            self.trvHandlerLogger.debug(f'processAdvance [3]: Seconds To Next Schedule = \'{secondsToNextSchedule}\'\n{calcSecondsLog}')

            self.globals['timers']['advanceCancel'][trvCtlrDevId] = threading.Timer(float(secondsToNextSchedule), self.processAdvanceCancel, [trvCtlrDevId, False])
            self.globals['timers']['advanceCancel'][trvCtlrDevId].setDaemon(True)
            self.globals['timers']['advanceCancel'][trvCtlrDevId].start()

            self.processHeatingSchedule(trvCtlrDevId)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def processAdvanceCancel(self, trvCtlrDevId, invokeProcessHeatingSchedule):

        try:
            if self.globals['trvc'][trvCtlrDevId]['advanceActive']:

                if trvCtlrDevId in self.globals['timers']['advanceCancel']:  # Cancel any existing advance cancel timer
                    self.globals['timers']['advanceCancel'][trvCtlrDevId].cancel()

                self.trvHandlerLogger.debug(
                    f'processAdvanceCancel [1]:\nRunning:\n{self.globals["schedules"][trvCtlrDevId]["running"]}\n\nDynamic:\n{self.globals["schedules"][trvCtlrDevId]["dynamic"]}\n\n')

                # Reset Schedule to previous running state
                self.globals['schedules'][trvCtlrDevId]['dynamic'] = collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['running'].items())).copy()

                self.trvHandlerLogger.debug(
                    f'processAdvanceCancel [2]:\nRunning:\n{self.globals["schedules"][trvCtlrDevId]["running"]}\n\nDynamic:\n{self.globals["schedules"][trvCtlrDevId]["dynamic"]}\n\n')

                self.globals['trvc'][trvCtlrDevId]['advanceActive'] = False
                self.globals['trvc'][trvCtlrDevId]['advanceStatusUi'] = ''
                self.globals['trvc'][trvCtlrDevId]['advanceActivatedTime'] = 'Inactive'
                self.globals['trvc'][trvCtlrDevId]['advanceToScheduleTime'] = 'Inactive'

                keyValueList = [
                        {'key': 'advanceActive', 'value': self.globals['trvc'][trvCtlrDevId]['advanceActive']},
                        {'key': 'advanceStatusUi', 'value': self.globals['trvc'][trvCtlrDevId]['advanceStatusUi']},
                        {'key': 'advanceActivatedTime', 'value': self.globals['trvc'][trvCtlrDevId]['advanceActivatedTime']},
                        {'key': 'advanceToScheduleTime', 'value': self.globals['trvc'][trvCtlrDevId]['advanceToScheduleTime']}
                    ]
                indigo.devices[trvCtlrDevId].updateStatesOnServer(keyValueList)

                if invokeProcessHeatingSchedule:
                    self.processHeatingSchedule(trvCtlrDevId)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def processBoost(self, trvCtlrDevId, boostMode, boostDeltaT, boostSetpoint, boostMinutes):

        try:
            self.trvHandlerLogger.debug(f'Boost invoked for Thermostat \'{indigo.devices[trvCtlrDevId].name}\': DeltaT = \'{boostDeltaT}\', Minutes = \'{boostMinutes}\'')

            self.processAdvanceCancel(trvCtlrDevId, False)
            self.processExtendCancel(trvCtlrDevId, False)

            self.globals['trvc'][trvCtlrDevId]['boostMode'] = int(boostMode)
            self.globals['trvc'][trvCtlrDevId]['boostModeUi'] = BOOST_MODE_TRANSLATION[self.globals['trvc'][trvCtlrDevId]['boostMode']]
            self.globals['trvc'][trvCtlrDevId]['boostActive'] = True
            self.globals['trvc'][trvCtlrDevId]['boostDeltaT'] = float(boostDeltaT)
            self.globals['trvc'][trvCtlrDevId]['boostSetpoint'] = float(boostSetpoint)
            self.globals['trvc'][trvCtlrDevId]['boostMinutes'] = int(boostMinutes)
            self.globals['trvc'][trvCtlrDevId]['boostSetpointToRestore'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeat'])
            self.globals['trvc'][trvCtlrDevId]['boostSetpointInvokeRestore'] = False  # Gets set to True when Boost is cancelled

            startTime = datetime.datetime.now()
            endTime = startTime + datetime.timedelta(minutes=self.globals['trvc'][trvCtlrDevId]['boostMinutes'])

            self.globals['trvc'][trvCtlrDevId]['boostTimeStart'] = startTime.strftime('%H:%M')
            self.globals['trvc'][trvCtlrDevId]['boostTimeEnd'] = endTime.strftime('%H:%M')
            self.globals['trvc'][trvCtlrDevId]['boostStatusUi'] = f'{self.globals["trvc"][trvCtlrDevId]["boostTimeStart"]} - {self.globals["trvc"][trvCtlrDevId]["boostTimeEnd"]}'

            if self.globals['trvc'][trvCtlrDevId]['boostMode'] == BOOST_MODE_DELTA_T:
                self.globals['trvc'][trvCtlrDevId]['boostStatusUi'] = f'{self.globals["trvc"][trvCtlrDevId]["boostStatusUi"]} [DeltaT = +{self.globals["trvc"][trvCtlrDevId]["boostDeltaT"]}]'
                newSetpoint = float(self.globals['trvc'][trvCtlrDevId]['temperature']) + float(boostDeltaT)
                if newSetpoint > float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum']):
                    newSetpoint = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum'])
            else:  # BOOST_MODE_SETPOINT
                newSetpoint = float(self.globals['trvc'][trvCtlrDevId]['boostSetpoint'])                
                self.globals['trvc'][trvCtlrDevId]['boostStatusUi'] = f'{self.globals["trvc"][trvCtlrDevId]["boostStatusUi"]} [Setpoint = {newSetpoint}]'

            keyValueList = [{'key': 'boostActive', 'value': bool(self.globals['trvc'][trvCtlrDevId]['boostActive'])},
                            {'key': 'boostMode', 'value': int(self.globals['trvc'][trvCtlrDevId]['boostMode'])},
                            {'key': 'boostModeUi', 'value': self.globals['trvc'][trvCtlrDevId]['boostModeUi']},
                            {'key': 'boostStatusUi', 'value': self.globals['trvc'][trvCtlrDevId]['boostStatusUi']},
                            {'key': 'boostDeltaT', 'value': float(self.globals['trvc'][trvCtlrDevId]['boostDeltaT'])},
                            {'key': 'boostSetpoint', 'value': int(self.globals['trvc'][trvCtlrDevId]['boostSetpoint'])},
                            {'key': 'boostMinutes', 'value': int(self.globals['trvc'][trvCtlrDevId]['boostMinutes'])},
                            {'key': 'boostTimeStart', 'value': self.globals['trvc'][trvCtlrDevId]['boostTimeStart']},
                            {'key': 'boostTimeEnd', 'value': self.globals['trvc'][trvCtlrDevId]['boostTimeEnd']},
                            {'key': 'controllerMode', 'value': CONTROLLER_MODE_UI},
                            {'key': 'controllerModeUi', 'value':  CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_UI]},
                            {'key': 'setpointHeat', 'value': newSetpoint}]
            indigo.devices[trvCtlrDevId].updateStatesOnServer(keyValueList)

            self.globals['timers']['boost'][trvCtlrDevId] = threading.Timer(float(boostMinutes * 60), self.boostCancelTriggered, [trvCtlrDevId, True])
            self.globals['timers']['boost'][trvCtlrDevId].setDaemon(True)
            self.globals['timers']['boost'][trvCtlrDevId].start()

            if self.globals['trvc'][trvCtlrDevId]['pollingBoostEnabled'] != 0.0:
                # Initiate polling sequence and force immediate status update
                self.globals['trvc'][trvCtlrDevId]['pollingSeconds'] = float(self.globals['trvc'][trvCtlrDevId]['pollingBoostEnabled'])
                self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_TRIGGER_POLL, trvCtlrDevId, []])

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def processBoostCancel(self, trvCtlrDevId, invokeProcessHeatingSchedule):

        try:
            self.boostCancelTriggered(trvCtlrDevId, invokeProcessHeatingSchedule)
            
        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def boostCancelTriggered(self, trvCtlrDevId, invokeProcessHeatingSchedule):

        try:
            if self.globals['trvc'][trvCtlrDevId]['boostActive']:

                if trvCtlrDevId in self.globals['timers']['boost']:
                    self.globals['timers']['boost'][trvCtlrDevId].cancel()
                    self.trvHandlerLogger.debug(f'boostCancelTriggered timer cancelled for device \'{indigo.devices[trvCtlrDevId].name}\'')

                self.trvHandlerLogger.debug(f'Boost CANCEL processed for Thermostat \'{indigo.devices[trvCtlrDevId].name}\'')

                self.globals['trvc'][trvCtlrDevId]['boostActive'] = False
                self.globals['trvc'][trvCtlrDevId]['boostMode'] = BOOST_MODE_INACTIVE
                self.globals['trvc'][trvCtlrDevId]['boostModeUi'] = BOOST_MODE_TRANSLATION[self.globals['trvc'][trvCtlrDevId]['boostMode']]
                self.globals['trvc'][trvCtlrDevId]['boostDeltaT'] = float(0.0)
                self.globals['trvc'][trvCtlrDevId]['boostSetpoint'] = float(0.0)
                self.globals['trvc'][trvCtlrDevId]['boostMinutes'] = int(0)
                self.globals['trvc'][trvCtlrDevId]['boostTimeStart'] = 'Inactive'
                self.globals['trvc'][trvCtlrDevId]['boostTimeEnd'] = 'Inactive'
                self.globals['trvc'][trvCtlrDevId]['boostStatusUi'] = ''

                keyValueList = [{'key': 'boostActive', 'value': bool(self.globals['trvc'][trvCtlrDevId]['boostActive'])},
                                {'key': 'boostMode', 'value': int(self.globals['trvc'][trvCtlrDevId]['boostMode'])},
                                {'key': 'boostModeUi', 'value': self.globals['trvc'][trvCtlrDevId]['boostModeUi']},
                                {'key': 'boostStatusUi', 'value': self.globals['trvc'][trvCtlrDevId]['boostStatusUi']},
                                {'key': 'boostDeltaT', 'value': float(self.globals['trvc'][trvCtlrDevId]['boostDeltaT'])},
                                {'key': 'boostSetpoint', 'value': int(self.globals['trvc'][trvCtlrDevId]['boostSetpoint'])},
                                {'key': 'boostMinutes', 'value': int(self.globals['trvc'][trvCtlrDevId]['boostMinutes'])},
                                {'key': 'boostTimeStart', 'value': self.globals['trvc'][trvCtlrDevId]['boostTimeStart']},
                                {'key': 'boostTimeEnd', 'value': self.globals['trvc'][trvCtlrDevId]['boostTimeEnd']}]
                indigo.devices[trvCtlrDevId].updateStatesOnServer(keyValueList)

                if invokeProcessHeatingSchedule:
                    self.globals['trvc'][trvCtlrDevId]['boostSetpointInvokeRestore'] = True
                    self.processHeatingSchedule(trvCtlrDevId)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def processExtend(self, trvCtlrDevId, extendIncrementMinutes, extendMaximumMinutes):

        try:
            self.trvHandlerLogger.debug(
                f'Extend processed for Thermostat \'{indigo.devices[trvCtlrDevId].name}\': Increment Minutes = \'{extendIncrementMinutes}\', Maximum Minutes = \'{extendMaximumMinutes}\'')

            self.processAdvanceCancel(trvCtlrDevId, False)
            self.processBoostCancel(trvCtlrDevId, False)

            if trvCtlrDevId in self.globals['timers']['heatingSchedules']:
                self.globals['timers']['heatingSchedules'][trvCtlrDevId].cancel()

            extendMinutes = self.globals['trvc'][trvCtlrDevId]['extendMinutes'] + extendIncrementMinutes
            if (extendMinutes > extendMaximumMinutes) or self.globals['trvc'][trvCtlrDevId]['extendLimitReached']:
                self.processExtendCancel(trvCtlrDevId, True)
                return

            self.trvHandlerLogger.debug(f'processAdvance [0]:\nRunning:\n{self.globals["schedules"][trvCtlrDevId]["running"]}\n\nDynamic:\n{self.globals["schedules"][trvCtlrDevId]["dynamic"]}\n\n')

            scheduleList = self.globals['schedules'][trvCtlrDevId]['running'].copy()

            # trvcDev = indigo.devices[trvCtlrDevId]

            initialiseHeatingScheduleLog = f'\n\n{"|" * 80}'
            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n||  Device: {indigo.devices[trvCtlrDevId].name}\n||  Method: processExtend'
            for key, value in scheduleList.items():
                # scheduleTime = int(key)
                scheduleTimeUi = f'{value[SCHEDULE_TIME_UI]}'
                scheduleSetpoint = float(value[SCHEDULE_SETPOINT])
                scheduleId = int(value[SCHEDULE_ID])
                # scheduleActive = bool(value[SCHEDULE_ACTIVE])
                initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n||  Time = {scheduleTimeUi}, Setpoint = {scheduleSetpoint}, Id = {scheduleId}'
            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n||  ScheduleList Length = {len(scheduleList)}, ScheduleList Type = {type(scheduleList)}'

            ct = int(datetime.datetime.now().strftime('%H%M%S'))

            def calcExtension(nextSchedule, _nextSchedulePlusOne, _extendMinutes):

                def evalExtensionSeconds(et):  # e.g.: 141545
                    etHH = et // 10000  # e.g.: 14
                    etTemp = et % 10000  # e.g. 1545
                    etMM = etTemp // 100  # e.g.: 15
                    etSS = etTemp % 100  # e.g.: 45
                    etSeconds = (etHH * 3600) + (etMM * 60) + etSS
                    return etSeconds

                nextScheduleSeconds = evalExtensionSeconds(nextSchedule)
                nextSchedulePlusOneSeconds = evalExtensionSeconds(_nextSchedulePlusOne)
                nextScheduleTimeLimitSeconds = nextSchedulePlusOneSeconds - 300  # Minus 5 minutes

                limitFlag = False

                extendedScheduleSeconds = nextScheduleSeconds + (_extendMinutes * 60)

                if extendedScheduleSeconds > nextScheduleTimeLimitSeconds:
                    extendedScheduleSeconds = nextScheduleTimeLimitSeconds
                    limitFlag = True

                extendedScheduleTimeHH = extendedScheduleSeconds // 3600
                extendedScheduleTimeTemp = extendedScheduleSeconds % 3600
                extendedScheduleTimeMM = extendedScheduleTimeTemp // 60
                extendedScheduleTimeSS = extendedScheduleTimeTemp % 60
                extendedScheduleTime = (extendedScheduleTimeHH * 10000) + (extendedScheduleTimeMM * 100) + extendedScheduleTimeSS

                return int(extendedScheduleTime), bool(limitFlag)

            currentScheduleTime = max(k for k in scheduleList if k <= ct)
            currentSchedule = scheduleList[currentScheduleTime]
            currentScheduleActiveUi = 'Start' if bool(currentSchedule[SCHEDULE_ACTIVE]) else 'End'
            # currentScheduleId = int(currentSchedule[SCHEDULE_ID])

            originalNextScheduleTime = min(k for k in scheduleList if k >= ct)
            if originalNextScheduleTime == 240000:
                self.trvHandlerLogger.info(f'Extend request for \'{indigo.devices[trvCtlrDevId].name}\' ignored; Can\'t  Extend beyond end-of-day (24:00)')
                return
            else:
                nextSchedulePlusOne = min(k for k in scheduleList if k > originalNextScheduleTime)
            extendedNextScheduleTime, self.globals['trvc'][trvCtlrDevId]['extendLimitReached'] = calcExtension(originalNextScheduleTime, nextSchedulePlusOne, extendMinutes)

            self.trvHandlerLogger.debug(
                f'processExtend: Original Next Schedule Time = \'{originalNextScheduleTime}\', Next Schedule Plus One Time =  \'{nextSchedulePlusOne}\', Extend Minutes Reached = \'{extendMinutes}\', Extended Next Schedule Time = \'{extendedNextScheduleTime}\', Extend Limit = {self.globals["trvc"][trvCtlrDevId]["extendLimitReached"]}')

            # extendedPreviousScheduleTime = max(k for k in scheduleList if k <= extendedNextScheduleTime)

            originalNextSchedule = scheduleList[originalNextScheduleTime]
            extendedNextScheduleSetpoint = float(originalNextSchedule[SCHEDULE_SETPOINT])
            extendedNextScheduleScheduleId = int(originalNextSchedule[SCHEDULE_ID])
            extendedNextScheduleScheduleActive = bool(originalNextSchedule[SCHEDULE_ACTIVE])
            extendedNextScheduleScheduleActiveUi = 'Start' if extendedNextScheduleScheduleActive else 'End'

            originalNextScheduleTimeWork = f'0{originalNextScheduleTime}'[-6:]
            originalNextScheduleTimeUi = f'{originalNextScheduleTimeWork[0:2]}:{originalNextScheduleTimeWork[2:4]}'

            extendedNextScheduleTimeWork = f'0{extendedNextScheduleTime}'[-6:]
            extendedNextScheduleTimeUi = f'{extendedNextScheduleTimeWork[0:2]}:{extendedNextScheduleTimeWork[2:4]}'

            del scheduleList[originalNextScheduleTime]

            scheduleList[extendedNextScheduleTime] = (extendedNextScheduleTimeUi, extendedNextScheduleSetpoint, extendedNextScheduleScheduleId, extendedNextScheduleScheduleActive)

            self.globals['schedules'][trvCtlrDevId]['dynamic'] = collections.OrderedDict(sorted(scheduleList.items())).copy()

            self.trvHandlerLogger.debug(f'processExtend [1]:\nRunning:\n{self.globals["schedules"][trvCtlrDevId]["running"]}\n\nDynamic:\n{self.globals["schedules"][trvCtlrDevId]["dynamic"]}\n\n')
            
            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n{"||" * 80}\n\n'
            self.trvHandlerLogger.debug(initialiseHeatingScheduleLog)

            self.globals['trvc'][trvCtlrDevId]['extendActive'] = True

            self.globals['trvc'][trvCtlrDevId]['extendIncrementMinutes'] = int(extendIncrementMinutes)
            self.globals['trvc'][trvCtlrDevId]['extendMinutes'] = int(extendMinutes)
            self.globals['trvc'][trvCtlrDevId]['extendMaximumMinutes'] = int(extendMaximumMinutes)

            startTime = datetime.datetime.now()

            self.globals['trvc'][trvCtlrDevId]['extendActivatedTime'] = startTime.strftime('%H:%M')
            self.globals['trvc'][trvCtlrDevId]['extendScheduleOriginalTime'] = originalNextScheduleTimeUi
            self.globals['trvc'][trvCtlrDevId]['extendScheduleNewTime'] = extendedNextScheduleTimeUi

            self.globals['trvc'][trvCtlrDevId]['extendStatusUi'] = f'S{extendedNextScheduleScheduleId} \'{extendedNextScheduleScheduleActiveUi} at {self.globals["trvc"][trvCtlrDevId]["extendScheduleOriginalTime"]} => {self.globals["trvc"][trvCtlrDevId]["extendScheduleNewTime"]}\' at {self.globals["trvc"][trvCtlrDevId]["extendActivatedTime"]}'
            keyValueList = [
                    {'key': 'extendActive', 'value': self.globals['trvc'][trvCtlrDevId]['extendActive']},
                    {'key': 'extendActivatedTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendActivatedTime']},
                    {'key': 'extendStatusUi', 'value': self.globals['trvc'][trvCtlrDevId]['extendStatusUi']},
                    {'key': 'extendMinutes', 'value': self.globals['trvc'][trvCtlrDevId]['extendMinutes']},
                    {'key': 'extendActivatedTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendActivatedTime']},
                    {'key': 'extendScheduleOriginalTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendScheduleOriginalTime']},
                    {'key': 'extendScheduleNewTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendScheduleNewTime']},
                    {'key': 'extendLimitReached', 'value': self.globals['trvc'][trvCtlrDevId]['extendLimitReached']}
                ]
            indigo.devices[trvCtlrDevId].updateStatesOnServer(keyValueList)

            self.trvHandlerLogger.info(
                f'Extending current \'{currentScheduleActiveUi}\' schedule for \'{indigo.devices[trvCtlrDevId].name}\': Next \'{extendedNextScheduleScheduleActiveUi}\' Schedule Time of \'{self.globals["trvc"][trvCtlrDevId]["extendScheduleOriginalTime"]}\' altered to \'{self.globals["trvc"][trvCtlrDevId]["extendScheduleNewTime"]}\'')

            self.processHeatingSchedule(trvCtlrDevId)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def processExtendCancel(self, trvCtlrDevId, invokeProcessHeatingSchedule):

        try:
            if self.globals['trvc'][trvCtlrDevId]['extendActive']:

                self.trvHandlerLogger.debug(
                    f'processExtendCancel [1]:\nRunning:\n{self.globals["schedules"][trvCtlrDevId]["running"]}\n\nDynamic:\n{self.globals["schedules"][trvCtlrDevId]["dynamic"]}\n\n')
                
                self.globals['schedules'][trvCtlrDevId]['dynamic'] = collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['running'].items())).copy()  # Reset Schedule to previous running state

                self.trvHandlerLogger.debug(
                    f'processExtendCancel [2]:\nRunning:\n{self.globals["schedules"][trvCtlrDevId]["running"]}\n\nDynamic:\n{self.globals["schedules"][trvCtlrDevId]["dynamic"]}\n\n')

                self.globals['trvc'][trvCtlrDevId]['extendActive'] = False
                self.globals['trvc'][trvCtlrDevId]['extendActivatedTime'] = ''
                self.globals['trvc'][trvCtlrDevId]['extendStatusUi'] = ''
                self.globals['trvc'][trvCtlrDevId]['extendMinutes'] = 0
                self.globals['trvc'][trvCtlrDevId]['extendActivatedTime'] = 'Inactive'
                self.globals['trvc'][trvCtlrDevId]['extendScheduleOriginalTime'] = 'Inactive'
                self.globals['trvc'][trvCtlrDevId]['extendScheduleNewTime'] = 'Inactive'
                self.globals['trvc'][trvCtlrDevId]['extendLimitReached'] = False
                keyValueList = [
                        {'key': 'extendActive', 'value': self.globals['trvc'][trvCtlrDevId]['extendActive']},
                        {'key': 'extendActivatedTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendActivatedTime']},
                        {'key': 'extendStatusUi', 'value': self.globals['trvc'][trvCtlrDevId]['extendStatusUi']},
                        {'key': 'extendActivatedTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendActivatedTime']},
                        {'key': 'extendScheduleOriginalTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendScheduleOriginalTime']},
                        {'key': 'extendScheduleNewTime', 'value': self.globals['trvc'][trvCtlrDevId]['extendScheduleNewTime']},
                        {'key': 'extendLimitReached', 'value': self.globals['trvc'][trvCtlrDevId]['extendLimitReached']}
                    ]
                indigo.devices[trvCtlrDevId].updateStatesOnServer(keyValueList)

                self.trvHandlerLogger.info(f'Extend schedule cancelled for \'{indigo.devices[trvCtlrDevId].name}\'')

                if invokeProcessHeatingSchedule:
                    self.processHeatingSchedule(trvCtlrDevId)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def processHeatingSchedule(self, trvCtlrDevId):
        try:
            schedulingEnabled = self.globals['trvc'][trvCtlrDevId]['schedule1Enabled'] or self.globals['trvc'][trvCtlrDevId]['schedule2Enabled'] or self.globals['trvc'][trvCtlrDevId]['schedule3Enabled'] or self.globals['trvc'][trvCtlrDevId]['schedule4Enabled']

            scheduleList = collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['dynamic'].items()))

            if trvCtlrDevId in self.globals['timers']['heatingSchedules']:
                self.globals['timers']['heatingSchedules'][trvCtlrDevId].cancel()

            trvcDev = indigo.devices[trvCtlrDevId]

            initialiseHeatingScheduleLog = f'\n\n{"@" * 80}'
            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n@@  Device: {indigo.devices[trvCtlrDevId].name}\n@@  Method: processHeatingSchedule'
            for key, value in scheduleList.items():
                # scheduleTime = int(key)  # HHMMSS
                scheduleTimeUi = f'{value[0]}'  # 'HH:MM'
                scheduleSetpoint = float(value[1])
                scheduleId = value[2]

                initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n@@  Time = {scheduleTimeUi}, Setpoint = {scheduleSetpoint}, Id = {scheduleId}'

            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n@@  ScheduleList Length = {len(scheduleList)}, ScheduleList Type = {type(scheduleList)}'

            # ctPrecision = int(datetime.datetime.now().strftime('%H%M%S'))
            # ct = ctPrecision / 100  # HHMM i.e remove seconds

            ct = int(datetime.datetime.now().strftime('%H%M%S'))

            previousSchedule = 0
            nextSchedule = 0

            for key, value in scheduleList.items():
                if key <= ct:
                    previousSchedule = key
                else:
                    if key == 240000 and nextSchedule == 0:
                        nextSchedule = 240000
                    else:
                        if nextSchedule == 0:
                            nextSchedule = key

            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n@@\n@@  CT={ct}, Prev={previousSchedule}, Next={nextSchedule}'

            schedule1Active = False
            schedule2Active = False
            schedule3Active = False
            schedule4Active = False

            if nextSchedule < 240000:
                if previousSchedule == 0:  # i.e. start of day
                    schedule = scheduleList[previousSchedule]

                    initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n@@  Current Time = {ct}, No schedule active'

                    # self.globals['trvc'][trvCtlrDevId]['zwavePendingSetpoint'] = True

                    self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(schedule[SCHEDULE_SETPOINT])
                    self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_AUTO
                    keyValueList = [
                            {'key': 'schedule1Active', 'value': schedule1Active},
                            {'key': 'schedule2Active', 'value': schedule2Active},
                            {'key': 'schedule3Active', 'value': schedule3Active},
                            {'key': 'schedule4Active', 'value': schedule4Active},
                            {'key': 'controllerMode', 'value': CONTROLLER_MODE_AUTO},
                            {'key': 'controllerModeUi', 'value': CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_AUTO]},
                            {'key': 'setpointHeat', 'value': self.globals['trvc'][trvCtlrDevId]['setpointHeat']}
                        ]
                    trvcDev.updateStatesOnServer(keyValueList)
                    self.trvHandlerLogger.debug(f'processHeatingSchedule: Adjusting TRV Controller \'{trvcDev.name}\' Setpoint Heat to {self.globals["trvc"][trvCtlrDevId]["setpointHeat"]}')

                else:
                    schedule = scheduleList[previousSchedule]

                    if schedule[SCHEDULE_ACTIVE]:
                        initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n@@  Current Time = {ct}, Current Schedule started at {previousSchedule} = {schedule}'
                        if schedule[SCHEDULE_ID] == 1:
                            schedule1Active = True
                        elif schedule[SCHEDULE_ID] == 2:
                            schedule2Active = True
                        elif schedule[SCHEDULE_ID] == 3:
                            schedule3Active = True
                        elif schedule[SCHEDULE_ID] == 4:
                            schedule4Active = True
                    else:
                        initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n@@  Current Time = {ct}, Last Schedule finished at {previousSchedule} = {schedule}'

                    # self.globals['trvc'][trvCtlrDevId]['zwavePendingSetpoint'] = True

                    self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(schedule[SCHEDULE_SETPOINT])
                    self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_AUTO
                    keyValueList = [
                            {'key': 'schedule1Active', 'value': schedule1Active},
                            {'key': 'schedule2Active', 'value': schedule2Active},
                            {'key': 'schedule3Active', 'value': schedule3Active},
                            {'key': 'schedule4Active', 'value': schedule4Active},
                            {'key': 'controllerMode', 'value': CONTROLLER_MODE_AUTO},
                            {'key': 'controllerModeUi', 'value': CONTROLLER_MODE_TRANSLATION[CONTROLLER_MODE_AUTO]},
                            {'key': 'setpointHeat', 'value': self.globals['trvc'][trvCtlrDevId]['setpointHeat']}
                        ]
                    trvcDev.updateStatesOnServer(keyValueList)
                    self.trvHandlerLogger.debug(f'processHeatingSchedule: Adjusting TRV Controller \'{trvcDev.name}\' Setpoint Heat to {self.globals["trvc"][trvCtlrDevId]["setpointHeat"]}')

                schedule = scheduleList[nextSchedule]
                initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n@@  Next Schedule starts at {nextSchedule} = {schedule}'

                secondsToNextSchedule, calcSecondsLog = calcSeconds(nextSchedule, ct)
                initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n@@  calcSeconds: {calcSecondsLog}'

                self.trvHandlerLogger.debug(f'processHeatingSchedule: CALCSECONDS [{type(secondsToNextSchedule)}] =  \'{secondsToNextSchedule}\'')

                self.globals['timers']['heatingSchedules'][trvCtlrDevId] = threading.Timer(float(secondsToNextSchedule), self.heatingScheduleTriggered, [trvCtlrDevId])
                self.globals['timers']['heatingSchedules'][trvCtlrDevId].setDaemon(True)
                self.globals['timers']['heatingSchedules'][trvCtlrDevId].start()

                nsetTemp = f'0{nextSchedule}'[-6:]  # e.g 91045 > 091045
                nsetUi = f'{nsetTemp[0:2]}:{nsetTemp[2:4]}'  # e.g. 09:10

                self.globals['trvc'][trvCtlrDevId]['nextScheduleExecutionTime'] = nsetUi
                trvcDev.updateStateOnServer(key='nextScheduleExecutionTime', value=self.globals['trvc'][trvCtlrDevId]['nextScheduleExecutionTime'])

            else:

                if schedulingEnabled:
                    schedule = scheduleList[nextSchedule]
                    self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(schedule[SCHEDULE_SETPOINT])
                    self.trvHandlerLogger.debug(f'processHeatingSchedule: Adjusting TRV Controller \'{trvcDev.name}\' Setpoint Heat to {self.globals["trvc"][trvCtlrDevId]["setpointHeat"]}')
                else:
                    if self.globals['trvc'][trvCtlrDevId]['boostSetpointInvokeRestore']:
                        self.globals['trvc'][trvCtlrDevId]['boostSetpointInvokeRestore'] = False
                        self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = self.globals['trvc'][trvCtlrDevId]['boostSetpointToRestore']
                        self.globals['trvc'][trvCtlrDevId]['boostSetpointToRestore'] = 0.0
                        self.trvHandlerLogger.debug(
                            f'processHeatingSchedule: Restoring TRV Controller \'{trvcDev.name}\' Setpoint to pre-boost value {self.globals["trvc"][trvCtlrDevId]["setpointHeat"]}')
                    else:
                        self.trvHandlerLogger.debug(f'processHeatingSchedule: Leaving TRV Controller \'{trvcDev.name}\' Setpoint at {self.globals["trvc"][trvCtlrDevId]["setpointHeat"]}')

                keyValueList = []
                if schedulingEnabled:
                    schedule = scheduleList[nextSchedule]
                    self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_AUTO
                    self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(schedule[SCHEDULE_SETPOINT])
                    self.globals['trvc'][trvCtlrDevId]['nextScheduleExecutionTime'] = 'All enabled schedules completed'
                else:
                    self.globals['trvc'][trvCtlrDevId]['controllerMode'] = CONTROLLER_MODE_UI
                    self.globals['trvc'][trvCtlrDevId]['nextScheduleExecutionTime'] = 'No schedules enabled'
                keyValueList.append({'key': 'controllerMode', 'value': self.globals['trvc'][trvCtlrDevId]['controllerMode']})
                keyValueList.append({'key': 'setpointHeat', 'value': self.globals['trvc'][trvCtlrDevId]['setpointHeat']})
                keyValueList.append({'key': 'controllerModeUi', 'value': CONTROLLER_MODE_TRANSLATION[self.globals['trvc'][trvCtlrDevId]['controllerMode']]})
                keyValueList.append({'key': 'schedule1Active', 'value': schedule1Active})
                keyValueList.append({'key': 'schedule2Active', 'value': schedule2Active})
                keyValueList.append({'key': 'schedule3Active', 'value': schedule3Active})
                keyValueList.append({'key': 'schedule4Active', 'value': schedule4Active})
                keyValueList.append({'key': 'nextScheduleExecutionTime', 'value': self.globals['trvc'][trvCtlrDevId]['nextScheduleExecutionTime']})
                trvcDev.updateStatesOnServer(keyValueList)

                initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n@@  Current Time = {ct}, No schedule active or pending'

            if indigo.devices[self.globals['trvc'][trvCtlrDevId]['trvDevId']].model == 'Thermostat (Spirit)':
                if schedulingEnabled:
                    if schedule1Active or schedule2Active or schedule3Active or schedule4Active:
                        pollingSeconds = self.globals['trvc'][trvCtlrDevId]['pollingScheduleActive']
                    else:
                        pollingSeconds = self.globals['trvc'][trvCtlrDevId]['pollingScheduleInactive']
                else:
                    pollingSeconds = self.globals['trvc'][trvCtlrDevId]['pollingSchedulesNotEnabled']

                if pollingSeconds != 0.0:
                    if 'pollingSeconds' not in self.globals['trvc'][trvCtlrDevId] or self.globals['trvc'][trvCtlrDevId]['pollingSeconds'] == 0.0 or self.globals['trvc'][trvCtlrDevId]['pollingSeconds'] != pollingSeconds:
                        # Initiate polling sequence and force immediate status update
                        self.globals['trvc'][trvCtlrDevId]['pollingSeconds'] = float(pollingSeconds)
                        self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_TRIGGER_POLL, trvCtlrDevId, []])

            initialiseHeatingScheduleLog = initialiseHeatingScheduleLog + f'\n{"@" * 80}\n\n'
            self.trvHandlerLogger.debug(initialiseHeatingScheduleLog)

            self.controlTrv(trvCtlrDevId)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def heatingScheduleTriggered(self, trvCtlrDevId):

        try:
            time.sleep(2)  # wait 2 seconds

            self.trvHandlerLogger.info(f'Schedule Change Triggered for \'{indigo.devices[trvCtlrDevId].name}\'')

            self.processExtendCancel(trvCtlrDevId, False)

            self.processHeatingSchedule(trvCtlrDevId)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def resetScheduleToDeviceDefaults(self, trvCtlrDevId):
        try:
            trvcDev = indigo.devices[trvCtlrDevId]
            if trvcDev.enabled:
                self.trvHandlerLogger.info(f'Resetting schedules to default values for TRV Controller \'{trvcDev.name}\'')
                indigo.device.enable(trvcDev.id, value=False)  # disable
                time.sleep(5)
                indigo.device.enable(trvcDev.id, value=True)  # enable

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def restateSchedules(self):
        try:
            for trvcDev in indigo.devices.iter('self'):
                if trvcDev.enabled:
                    self.trvHandlerLogger.info(f'Forcing restatement of schedules to default values for TRV Controller \'{trvcDev.name}\'')
                    indigo.device.enable(trvcDev.id, value=False)  # disable
                    time.sleep(5)
                    indigo.device.enable(trvcDev.id, value=True)  # enable
                    time.sleep(2)

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    # noinspection PyUnusedLocal
    def updateAllCsvFilesViaPostgreSQL(self, trvCtlrDevId, overrideDefaultRetentionHours, overrideCsvFilePrefix):

        try:
            # if not self.globals['config']['csvPostgresqlEnabled'] or not self.psycopg2_imported or not self.globals['trvc'][trvCtlrDevId]['updateAllCsvFilesViaPostgreSQL']:
            if not self.globals['config']['csvPostgresqlEnabled'] or not self.globals['trvc'][trvCtlrDevId]['updateAllCsvFilesViaPostgreSQL']:
                return

            self._updateCsvFileViaPostgreSQL(trvCtlrDevId, overrideDefaultRetentionHours, overrideCsvFilePrefix, 'setpointHeat')                
            self._updateCsvFileViaPostgreSQL(trvCtlrDevId, overrideDefaultRetentionHours, overrideCsvFilePrefix, 'temperatureTrv')                
            self._updateCsvFileViaPostgreSQL(trvCtlrDevId, overrideDefaultRetentionHours, overrideCsvFilePrefix, 'setpointHeatTrv')                
            if self.globals['trvc'][trvCtlrDevId]['valveDevId'] != 0:
                self._updateCsvFileViaPostgreSQL(trvCtlrDevId, overrideDefaultRetentionHours, overrideCsvFilePrefix, 'valvePercentageOpen')                
            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:
                self._updateCsvFileViaPostgreSQL(trvCtlrDevId, overrideDefaultRetentionHours, overrideCsvFilePrefix, 'temperatureRemote')                
                if self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl']:
                    self._updateCsvFileViaPostgreSQL(trvCtlrDevId, overrideDefaultRetentionHours, overrideCsvFilePrefix, 'setpointHeatRemote')                

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def updateAllCsvFiles(self, trvCtlrDevId):

        try:
            if not self.globals['config']['csvStandardEnabled'] or self.globals['trvc'][trvCtlrDevId]['csvCreationMethod'] != 1:  # Standard CSV Output
                return

            self.updateCsvFile(trvCtlrDevId, 'setpointHeat', float(self.globals['trvc'][trvCtlrDevId]['setpointHeat']))                
            self.updateCsvFile(trvCtlrDevId, 'temperatureTrv', float(self.globals['trvc'][trvCtlrDevId]['temperatureTrv']))                
            self.updateCsvFile(trvCtlrDevId, 'setpointHeatTrv', float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']))                
            if self.globals['trvc'][trvCtlrDevId]['valveDevId'] != 0:
                self.updateCsvFile(trvCtlrDevId, 'valvePercentageOpen', int(self.globals['trvc'][trvCtlrDevId]['valvePercentageOpen']))                
            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0:
                self.updateCsvFile(trvCtlrDevId, 'temperatureRemote', float(self.globals['trvc'][trvCtlrDevId]['temperatureRemote']))                
                if self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl']:
                    self.updateCsvFile(trvCtlrDevId, 'setpointHeatRemote', float(self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote']))                

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def updateCsvFile(self, trvCtlrDevId, stateName, updateValue):

        try:
            if not self.globals['config']['csvStandardEnabled'] or self.globals['trvc'][trvCtlrDevId]['csvCreationMethod'] != 1:  # Standard CSV Output
                return

            dateTimeNow = datetime.datetime.now()
            dateTimeNowStr = dateTimeNow.strftime("%Y-%m-%d %H:%M:%S.%f")

            checkTime = dateTimeNow - datetime.timedelta(hours=self.globals['trvc'][trvCtlrDevId]['csvRetentionPeriodHours'])
            checkTimeStr = checkTime.strftime("%Y-%m-%d %H:%M:%S.%f")

            csvShortName = self.globals['trvc'][trvCtlrDevId]['csvShortName']
            csvFileNamePathPrefix = f'{self.globals["config"]["csvPath"]}/{self.globals["config"]["csvPrefix"]}'

            csvFilename = f'{csvFileNamePathPrefix}_{csvShortName}_{stateName}.csv'

            headerName = f'{indigo.devices[trvCtlrDevId].name} - {stateName}'

            dataIn = []
            try:
                with open(csvFilename) as csvFileIn:
                    for line in csvFileIn:
                        line = line.strip()  # or some other pre-processing
                        dataIn.append(line)
                if len(dataIn) > 0:
                    dataIn.pop(0)  # Remove header
            except IOError:
                pass  # IO Error can validly occur if file hasn't yet been created

            csvFileOut = open(csvFilename, 'w')

            self.trvHandlerLogger.debug(f'CSV FILE NAME = \'{csvFilename}\', Time = \'{checkTimeStr}\', State = \'{stateName}\', Value = \'{updateValue}\'')

            headerName = headerName.replace(',', '_')  # Replace any commas with underscore to avoid CSV file problems
            csvFileOut.write(f'Timestamp,{headerName}\n')  # Write out header

            # droppedRowsFlag = False
            droppedRow = ''
            # writtenRowCount = 0
            firstRowWrittenFlag = False 
            for row in dataIn:
                if row[0:26] < checkTimeStr:  # e.g. 2017-04-09 17:26:13.956000
                    # droppedRowsFlag = True
                    droppedRow = row
                    continue
                elif row[0:26] == checkTimeStr:
                    pass
                else:
                    if not firstRowWrittenFlag:
                        firstRowWrittenFlag = True
                        if droppedRow != '':
                            firstRow = checkTimeStr + droppedRow[26:]
                        else:
                            firstRow = checkTimeStr + row[26:]
                        csvFileOut.write(f'{firstRow}\n')  # Output modified CSV data line
                csvFileOut.write(f'{row}\n')  # Output CSV data line as not older than retention limit
            csvFileOut.write(f'{dateTimeNowStr},{updateValue}\n')
            csvFileOut.close()

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

    def updateDeviceStates(self, trvCtlrDevId, command, updateList, sequence):  # noqa - command not used

        try:
            if indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['trvDevId'])].enabled is True:

                dev = indigo.devices[trvCtlrDevId]

                updateDeviceStatesLog = '\n\nXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX'
                updateDeviceStatesLog = updateDeviceStatesLog + '\nXX  Method: \'updateDeviceStates\''
                updateDeviceStatesLog = updateDeviceStatesLog + f'\nXX  Sequence: {sequence}'
                updateDeviceStatesLog = updateDeviceStatesLog + f'\nXX  Device: TRV CONTROLLER - \'{indigo.devices[trvCtlrDevId].name}\''

                updateDeviceStatesLog = updateDeviceStatesLog + '\nXX  List of states to be updated:'
                for itemToUpdate in updateList.items():
                    updateKey = itemToUpdate[0]
                    updateValue = itemToUpdate[1]
                    # updateInfo = updateInfo + f'Key = {updateKey}, Description = {UPDATE_TRANSLATION[updateKey]}, Value = {updateValue}\n'
                    updateDeviceStatesLog = updateDeviceStatesLog + f'\nXX    > Description = {UPDATE_TRANSLATION[updateKey]}, Value = {updateValue}'

                updateKeyValueList = []

                # YET TO BE DONE ...
                #   - UPDATE_ZWAVE_HVAC_OPERATION_MODE_ID = 4
                #   - UPDATE_ZWAVE_WAKEUP_INTERVAL = 6

                for itemToUpdate in updateList.items():
                    updateKey = itemToUpdate[0]
                    updateValue = itemToUpdate[1]

                    if updateKey == UPDATE_CONTROLLER_HVAC_OPERATION_MODE:
                        self.globals['trvc'][trvCtlrDevId]['hvacOperationMode'] = int(updateValue)
                        if dev.states['hvacOperationMode'] != int(updateValue):
                            updateKeyValueList.append({'key': 'hvacOperationMode', 'value': int(updateValue)})

                    # if updateKey == UPDATE_CONTROLLER_TEMPERATURE:
                    #     self.globals['trvc'][trvCtlrDevId]['temperature'] = float(updateValue)

                    elif updateKey == UPDATE_CONTROLLER_HEAT_SETPOINT:
                        self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(updateValue)
                        if dev.heatSetpoint != float(updateValue):
                            updateKeyValueList.append({'key': 'setpointHeat', 'value': float(updateValue)})  # Fixed in Version 1.7.4

                    elif updateKey == UPDATE_CONTROLLER_MODE:
                        self.globals['trvc'][trvCtlrDevId]['controllerMode'] = int(updateValue)
                        if dev.states['controllerMode'] != int(updateValue):
                            updateKeyValueList.append({'key': 'controllerMode', 'value': int(updateValue)})
                            updateKeyValueList.append({'key': 'controllerModeUi', 'value': CONTROLLER_MODE_TRANSLATION[int(updateValue)]})

                    elif updateKey == UPDATE_TRV_BATTERY_LEVEL:
                        self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'] = int(updateValue)
                        if dev.states['batteryLevelTrv'] != int(updateValue):
                            updateKeyValueList.append({'key': 'batteryLevelTrv', 'value': int(updateValue)})
                        if (self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'] != 0 and
                                self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'] < self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote']):
                            if dev.states['batteryLevel'] != int(updateValue):
                                updateKeyValueList.append({'key': 'batteryLevel', 'value': int(updateValue)})

                    elif updateKey == UPDATE_TRV_TEMPERATURE:
                        self.globals['trvc'][trvCtlrDevId]['temperatureTrv'] = float(updateValue)
                        if dev.states['temperatureTrv'] != float(updateValue):
                            updateKeyValueList.append({'key': 'temperatureTrv', 'value': float(updateValue)})
                            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] == 0:
                                updateKeyValueList.append(dict(key='temperatureInput1', value=float(updateValue), uiValue=f'{float(updateValue):.1f} °C'))
                                self.globals['trvc'][trvCtlrDevId]['temperature'] = float(updateValue)
                                updateKeyValueList.append({'key': 'temperature', 'value': float(updateValue)})
                                updateKeyValueList.append({'key': 'temperatureUi', 'value': f'{float(updateValue):.1f} °C'})
                            else:
                                updateKeyValueList.append({'key': 'temperatureInput2', 'value': float(updateValue), 'uiValue': f'{float(updateValue):.1f} °C'})
                                updateKeyValueList.append(
                                    {'key': 'temperatureUi', 'value': f'R: {self.globals["trvc"][trvCtlrDevId]["temperatureRemote"]:.1f} °C, T: {float(updateValue):.1f} °C'})

                    #                            if spirit
                    #                                if trv_newtemp > trv_oldtemp
                    #                                    if not calling_for_heat
                    #                                        if setpoint < trv_newtemp
                    #                                           if time since not calling_for_heat > poll_interval
                    #                                               potential problem - output to log
                    #
                    #
                    #

                    # if indigo.devices[trvCtlrDevId].model == 'Thermostat (Spirit)':
                    #     if (float(self.globals['trvc'][trvCtlrDevId]['setpointHeat']) <= float(self.globals['trvc'][trvCtlrDevId]['temperature'])) and not hvacFullPower:
                    #         if float(self.globals['trvc'][trvCtlrDevId]['setpointHeat'] < self.globals['trvc'][trvCtlrDevId]['temperatureTrv']:
                    #             if

                    elif updateKey == UPDATE_TRV_HVAC_OPERATION_MODE:
                        if dev.states['hvacOperationModeTrv'] != int(updateValue):
                            if int(updateValue) == RESET_TO_HVAC_HEAT:
                                updateValue = HVAC_HEAT
                                indigo.thermostat.setHvacMode(self.globals['trvc'][trvCtlrDevId]['trvDevId'], value=HVAC_HEAT)  # Force reset on TRV device
                            updateKeyValueList.append({'key': 'hvacOperationModeTrv', 'value': int(updateValue)})
                        self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'] = int(updateValue)

                        # NEXT BIT OF LOGIC NEEDS SOME ENHANCEMENT

                        if self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'] == HVAC_OFF:
                            self.globals['trvc'][trvCtlrDevId]['hvacOperationMode'] = HVAC_OFF
                            if dev.states['hvacOperationMode'] != int(HVAC_OFF):
                                updateKeyValueList.append({'key': 'hvacOperationMode', 'value': int(HVAC_OFF)})
                        elif self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'] == HVAC_HEAT:
                            self.globals['trvc'][trvCtlrDevId]['hvacOperationMode'] = HVAC_HEAT
                            if dev.states['hvacOperationMode'] != int(HVAC_HEAT):
                                updateKeyValueList.append({'key': 'hvacOperationMode', 'value': int(HVAC_HEAT)})

                    elif updateKey == UPDATE_TRV_HEAT_SETPOINT:
                        self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'] = float(updateValue)
                        if dev.states['setpointHeatTrv'] != float(updateValue):
                            updateKeyValueList.append({'key': 'setpointHeatTrv', 'value': float(updateValue)})
                        # if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] == 0 and self.globals['trvc'][trvCtlrDevId]['controllerMode'] != CONTROLLER_MODE_UI:
                        #     self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(updateValue)  # <============================================================================== Delta T processing needed ???
                        #     if dev.states['setpointHeat'] != float(updateValue):
                        #         updateKeyValueList.append({'key': 'setpointHeat', 'value':  float(updateValue)})

                    elif updateKey == UPDATE_TRV_HEAT_SETPOINT_FROM_DEVICE:
                        self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'] = float(updateValue)
                        if dev.states['setpointHeatTrv'] != float(updateValue):
                            updateKeyValueList.append({'key': 'setpointHeatTrv', 'value': float(updateValue)})
                        self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(updateValue)
                        if dev.states['setpointHeat'] != float(updateValue):
                            updateKeyValueList.append({'key': 'setpointHeat', 'value': float(updateValue)})
                        if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0 and self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl']:
                            self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote'] = float(updateValue)
                            if dev.states['setpointHeatRemote'] != float(updateValue):
                                updateKeyValueList.append({'key': 'setpointHeatRemote', 'value': float(updateValue)})

                    elif updateKey == UPDATE_REMOTE_BATTERY_LEVEL:
                        self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'] = int(updateValue)
                        if dev.states['batteryLevelRemote'] != float(updateValue):
                            updateKeyValueList.append({'key': 'batteryLevelRemote', 'value': int(updateValue)})
                        if (self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'] != 0 and
                                self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'] < self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv']):
                            if dev.states['batteryLevel'] != float(updateValue):
                                updateKeyValueList.append({'key': 'batteryLevel', 'value': int(updateValue)})

                    elif updateKey == UPDATE_REMOTE_TEMPERATURE:
                        updateValuePlusOffset = float(updateValue) + float(self.globals['trvc'][trvCtlrDevId]['remoteTempOffset'])  # Apply Offset
                        self.globals['trvc'][trvCtlrDevId]['temperatureRemote'] = float(updateValuePlusOffset)
                        self.globals['trvc'][trvCtlrDevId]['temperature'] = float(updateValuePlusOffset)
                        if dev.states['temperatureRemote'] != float(updateValuePlusOffset):
                            updateKeyValueList.append({'key': 'temperatureRemotePreOffset', 'value': float(updateValue)})
                            updateKeyValueList.append({'key': 'temperatureRemote', 'value': float(updateValuePlusOffset)})
                            updateKeyValueList.append({'key': 'temperature', 'value': float(updateValuePlusOffset)})
                            updateKeyValueList.append({'key': 'temperatureInput1', 'value': float(updateValuePlusOffset), 'uiValue': f'{float(updateValuePlusOffset):.1f} °C'})
                            updateKeyValueList.append(
                                {'key': 'temperatureUi', 'value': f'R: {float(updateValuePlusOffset):.1f} °C, T: {float(self.globals["trvc"][trvCtlrDevId]["temperatureTrv"]):.1f} °C'})

                            # if a Spirit:

                    elif updateKey == UPDATE_REMOTE_HEAT_SETPOINT_FROM_DEVICE:
                        setpoint = float(updateValue)
                        if float(setpoint) < float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum']):
                            setpoint = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum'])
                        elif float(setpoint) > float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum']):
                            setpoint = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum'])
                        self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote'] = float(setpoint)
                        self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(setpoint)
                        if dev.states['setpointHeatRemote'] != float(setpoint):
                            updateKeyValueList.append({'key': 'setpointHeatRemote', 'value': float(setpoint)})
                        if dev.states['setpointHeat'] != float(setpoint):
                            updateKeyValueList.append({'key': 'setpointHeat', 'value': float(setpoint)})

                    elif updateKey == UPDATE_ZWAVE_EVENT_RECEIVED_TRV:
                        updateKeyValueList.append({'key': 'zwaveEventReceivedDateTimeTrv', 'value': updateValue})

                    elif updateKey == UPDATE_ZWAVE_EVENT_RECEIVED_REMOTE:
                        updateKeyValueList.append({'key': 'zwaveEventReceivedDateTimeRemote', 'value': updateValue})

                    elif updateKey == UPDATE_ZWAVE_EVENT_SENT_TRV:
                        updateKeyValueList.append({'key': 'zwaveEventSentDateTimeTrv', 'value': updateValue})

                    elif updateKey == UPDATE_ZWAVE_EVENT_SENT_REMOTE:
                        updateKeyValueList.append({'key': 'zwaveEventSentDateTimeRemote', 'value': updateValue})

                    elif updateKey == UPDATE_EVENT_RECEIVED_REMOTE:
                        updateKeyValueList.append({'key': 'eventReceivedDateTimeRemote', 'value': updateValue})

                    elif updateKey == UPDATE_CONTROLLER_VALVE_PERCENTAGE:
                        self.globals['trvc'][trvCtlrDevId]['valvePercentageOpen'] = float(updateValue)
                        updateKeyValueList.append({'key': 'valvePercentageOpen', 'value': updateValue})

                # ##### LOGIC FOR POPP THERMOSTAT AMD SIMILAR #####

                # if not self.globals['trvc'][trvCtlrDevId]['trvSupportsHvacOperationMode']:
                #     if self.globals['trvc'][trvCtlrDevId]['setpointHeat'] <= self.globals['trvc'][trvCtlrDevId]['temperature']:
                #         self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'] = HVAC_OFF
                #         self.globals['trvc'][trvCtlrDevId]['hvacOperationMode'] = HVAC_OFF
                #         if dev.states['hvacOperationModeTrv'] !=  int(HVAC_OFF):
                #             updateKeyValueList.append({'key': 'hvacOperationModeTrv', 'value':  int(HVAC_OFF)})
                #         if dev.states['hvacOperationMode'] !=  int(HVAC_OFF):
                #             updateKeyValueList.append({'key': 'hvacOperationMode', 'value':  int(HVAC_OFF)})
                #     else:
                #         self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'] = HVAC_HEAT
                #         self.globals['trvc'][trvCtlrDevId]['hvacOperationMode'] = HVAC_HEAT
                #         if dev.states['hvacOperationModeTrv'] != int(HVAC_HEAT):
                #             updateKeyValueList.append({'key': 'hvacOperationModeTrv', 'value':  int(HVAC_HEAT)})
                #         if dev.states['hvacOperationMode'] != int(HVAC_HEAT):
                #             updateKeyValueList.append({'key': 'hvacOperationMode', 'value':  int(HVAC_HEAT)})

                if len(updateKeyValueList) > 0:
                    updateDeviceStatesLog = updateDeviceStatesLog + '\nXX  States to be updated in the TRV Controller device:'
                    for itemToUpdate in updateKeyValueList:
                        updateDeviceStatesLog = updateDeviceStatesLog + f'\nXX    > {itemToUpdate}'
                    dev.updateStatesOnServer(updateKeyValueList)
                else:
                    updateDeviceStatesLog = updateDeviceStatesLog + '\nXX  No States to be updated in the TRV Controller device:'

                updateDeviceStatesLog = updateDeviceStatesLog + '\nXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX\n\n'

                self.trvHandlerLogger.debug(updateDeviceStatesLog.encode('utf-8'))

                self.controlTrv(trvCtlrDevId)

                # self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_CONTROL_TRV, trvCtlrDevId, None])

                # self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0 ,CMD_CONTROL_HEATING_SOURCE, trvCtlrDevId, [self.globals['trvc'][trvCtlrDevId]['heatingId'], ]])

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

        # except:
            # self.trvHandlerLogger.error(f'Unexpected Exception detected in TRV Handler Thread [updateDeviceStates]. Line \'{sys.exc_traceback.tb_lineno}\' has error=\'{sys.exc_info()[0]}\'')

    def _updateCsvFileViaPostgreSQL(self, trvCtlrDevId, overrideDefaultRetentionHours, overrideCsvFilePrefix, stateName):
        try:
            # Dynamically create CSV files from SQL Logger

            postgreSQLSupported = False
            database = None
            user = 'UNKNOWN'  # Suppress PyCharm warning
            try:
                user = self.globals['config']['postgresqlUser']
                password = self.globals['config']['postgresqlPassword']

                database_open_string = f"pq://{user}:{password}@127.0.0.1:5432/indigo_history"

                database = postgresql.open(database_open_string)

                postgreSQLSupported = True

            except Exception as error_detail:  # TODO: Make sure this works using Python 3
                errString = f'{error_detail}'
                if errString.find('role') != -1 and errString.find('does not exist') != -1:
                    self.trvHandlerLogger.error(f'PostgreSQL user \'{user}\' (specified in plugin config) is invalid')
                else:
                    self.trvHandlerLogger.error(f'PostgreSQL not supported or connection attempt invalid. Reason: {error_detail}')

            if not postgreSQLSupported:
                return

            if overrideDefaultRetentionHours > 0:
                csvRetentionPeriodHours = overrideDefaultRetentionHours
            else:
                csvRetentionPeriodHours = self.globals['trvc'][trvCtlrDevId]['csvRetentionPeriodHours']
            dateTimeNow = datetime.datetime.now()
            checkTime = dateTimeNow - datetime.timedelta(hours=csvRetentionPeriodHours)
            checkTimeStr = checkTime.strftime("%Y-%m-%d %H:%M:%S.000000")

            selectString = f"SELECT ts, {stateName} FROM device_history_{trvCtlrDevId} WHERE ( ts >= '{checkTimeStr}' AND  {stateName} IS NOT NULL) ORDER BY ts"  # NOQA - YYYY-MM-DD HH:MM:SS
            ps = database.prepare(selectString)
            rows = ps()

            # At this point the entries have been retrieved for the selected period. They now need to be topped and tailed to be able to create nice graphs
            # The following select finds the entry just prior to the start of the period to use as the first value

            selectString2 = (f"SELECT ts, {stateName} FROM device_history_{trvCtlrDevId} WHERE ( ts < '{checkTimeStr}' AND {stateName} IS NOT NULL) ORDER BY ts DESC LIMIT 1")    # noqa [suppress no data sources help message] - YYYY-MM-DD HH:MM:SS
            ps2 = database.prepare(selectString2)
            droppedRows = ps2()
            if len(droppedRows) == 0:  # No entries yet available for whole period, so exit  TODO: Double Check this??? 16-April-2022
                return
            droppedRow = droppedRows[0]

            # self.trvHandlerLogger.info(f'LAST DROPPED ROW [{rowCount}] = \n{droppedRow}\n')

            csvShortName = self.globals['trvc'][trvCtlrDevId]['csvShortName']

            if overrideCsvFilePrefix != '':
                csvFilePrefix = overrideCsvFilePrefix
            else:
                csvFilePrefix = self.globals['config']['csvPrefix']

            csvFileNamePathPrefix = f'{self.globals["config"]["csvPath"]}/{csvFilePrefix}'

            csvFilename = f'{csvFileNamePathPrefix}_{csvShortName}_{stateName}.csv'

            headerName = f'{indigo.devices[trvCtlrDevId].name} - {stateName}'

            csvFileOut = open(csvFilename, 'w')

            self.trvHandlerLogger.debug(f'CSV FILE NAME = \'{csvFilename}\', Time = \'{checkTimeStr}\', State = \'{stateName}\'')

            headerName = headerName.replace(',', '_')  # Replace any commas with underscore to avoid CSV file problems
            csvFileOut.write(f'Timestamp,{headerName}\n')  # Write out header

            csvFileOut.write(f'{checkTimeStr},{droppedRow[1]}\n')

            for row in rows:
                timestamp = row[0].strftime("%Y-%m-%d %H:%M:%S.%f")
                dataValue = row[1]
                csvFileOut.write(f'{timestamp},{dataValue}\n')

            # The following processing finds the last entry and repeats it for the current time to provide the latest entry
            timestamp = dateTimeNow.strftime("%Y-%m-%d %H:%M:%S.999999")
            if len(rows) > 0:
                lastRow = rows[(len(rows) - 1)]
                dataValue = lastRow[1]
                csvFileOut.write(f'{timestamp},{dataValue}\n')
            else:
                csvFileOut.write(f'{timestamp},{droppedRow[1]}\n')

            csvFileOut.close()

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement