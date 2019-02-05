#! /usr/bin/env python
# -*- coding: utf-8 -*-
#
# TRV Handler © Autolog 2018
#

try:
    # noinspection PyUnresolvedReferences
    import indigo
except ImportError, e:
    pass

import collections
import datetime
import logging
try:
    import psycopg2  # PostgreSQL
except ImportError, e:
    pass

import Queue
import sys
import threading
import time

from constants import *

def calcSeconds(schedTime, nowTime):

    def evalSeconds(dt): # e.g.: 141545
        dtHH = dt / 10000  # e.g.: 14
        dtTemp = dt % 10000 # e.g. 1545
        dtMM = dtTemp / 100 # e.g.: 15
        dtSS = dtTemp % 100  # e.g.: 45
        dtSeconds = (dtHH * 3600) + (dtMM * 60) + dtSS
        return dtSeconds

    SchedSeconds = evalSeconds(schedTime)
    nowseconds = evalSeconds(nowTime)

    if nowseconds < SchedSeconds:
        result = SchedSeconds - nowseconds

        resulthh = result / 3600
        resultTemp = result % 3600
        resultmm = resultTemp / 60
        resultss = resultTemp % 60
        resultLog = u'Time to next schedule at {} [{}] from now {} [{}]: Seconds = {} = HH = {}, MM = {}, SS = {}'.format(schedTime, SchedSeconds, nowTime, nowseconds, result, resulthh, resultmm, resultss)

    else:
        result = 0
        resultLog = u'nothing to immediately schedule!!! : Time to next schedule at {} [{}] from now {} [{}]'.format(schedTime, SchedSeconds, nowTime, nowseconds)


    return result, resultLog

# noinspection PyUnresolvedReferences,PyPep8Naming
class ThreadTrvHandler(threading.Thread):

    # This class handles TRV processing

    def __init__(self, pluginGlobals, event):

        threading.Thread.__init__(self)

        self.globals = pluginGlobals

        self.trvHandlerLogger = logging.getLogger("Plugin.trvHandler")
        self.trvHandlerLogger.setLevel(self.globals['debug']['trvHandler'])

        self.methodTracer = logging.getLogger("Plugin.method")
        self.methodTracer.setLevel(self.globals['debug']['methodTrace'])

        self.trvHandlerLogger.debug(u"Debugging TRV Handler Thread")

        self.threadStop = event

        self.currentTimeUtc = None
        self.currentTimeDay = None
        self.toTimeUtc = None

    def convertUnicode(self, unicodeInput):
        if isinstance(unicodeInput, dict):
            return dict(
                [(self.convertUnicode(key), self.convertUnicode(value)) for key, value in unicodeInput.iteritems()])
        elif isinstance(unicodeInput, list):
            return [self.convertUnicode(element) for element in unicodeInput]
        elif isinstance(unicodeInput, unicode):
            return unicodeInput.encode('utf-8')
        else:
            return unicodeInput

    def run(self):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            # Initialise routine on thread start
            pass

            self.trvHandlerLogger.debug(u'TRV Handler Thread initialised')

            while not self.threadStop.is_set():
                try:
                    trvQueuedEntry = self.globals['queues']['trvHandler'].get(True, 5)

                    # trvQueuedEntry format:
                    #   - Priority
                    #   - Command
                    #   - Device
                    #   - Data

                    # self.trvHandlerLogger.debug(u'DEQUEUED MESSAGE = {}'.format(trvQueuedEntry))
                    trvQueuePriority, trvQueueSequence, trvCommand, trvCommandDevId, trvCommandPackage = trvQueuedEntry

                    if trvCommand == CMD_STOP_THREAD:
                        break  # Exit While loop and quit thread

                    self.currentTime = indigo.server.getTime()

                    # Check if monitoring / debug options have changed and if so set accordingly
                    if self.globals['debug']['previousTrvHandler'] != self.globals['debug']['trvHandler']:
                        self.globals['debug']['previousTrvHandler'] = self.globals['debug']['trvHandler']
                        self.trvHandlerLogger.setLevel(self.globals['debug']['trvHandler'])
                    if self.globals['debug']['previousMethodTrace'] != self.globals['debug']['methodTrace']:
                        self.globals['debug']['previousMethodTrace'] = self.globals['debug']['methodTrace']
                        self.methodTracer.setLevel(self.globals['debug']['methodTrace'])

                    if not trvCommandDevId is None:
                        self.trvHandlerLogger.debug(u'\nTRVHANDLER: \'{}\' DEQUEUED COMMAND \'{}\''.format(indigo.devices[trvCommandDevId].name, CMD_TRANSLATION[trvCommand]))
                    else:
                        self.trvHandlerLogger.debug(u'\nTRVHANDLER: DEQUEUED COMMAND \'{}\''.format(CMD_TRANSLATION[trvCommand]))

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
                        advanceType =  trvCommandPackage[0]
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

                    self.trvHandlerLogger.error(u'TRVHandler: \'{}\' command cannot be processed'.format(CMD_TRANSLATION[trvCommand]))

                except Queue.Empty:
                    pass
                except StandardError, err:
                    self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))  
                except:
                    self.trvHandlerLogger.error(u'Unexpected Exception detected in TRV Handler Thread. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, sys.exc_info()[0]))

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

        self.trvHandlerLogger.debug(u'TRV Handler Thread ended.')

    def processAdvance(self, trvCtlrDevId, advanceType):
        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')
        
            self.trvHandlerLogger.debug(u'Method: processAdvance')

            self.trvHandlerLogger.debug(u'processAdvance [0]: Type = [{}]\nRunning:\n{}\n\nDynamic:\n{}\n\n'.format(ADVANCE_TRANSLATION[advanceType], self.globals['schedules'][trvCtlrDevId]['running'], self.globals['schedules'][trvCtlrDevId]['dynamic']))
            
            if trvCtlrDevId in self.globals['timers']['heatingSchedules']:  # Cancel any existing heating schedule timer
                self.globals['timers']['heatingSchedules'][trvCtlrDevId].cancel()

            if trvCtlrDevId in self.globals['timers']['advanceCancel']:  # Cancel any existing advance cancel timer
                self.globals['timers']['advanceCancel'][trvCtlrDevId].cancel()

            self.processBoostCancel(trvCtlrDevId, False)
            self.processExtendCancel(trvCtlrDevId, False)

            scheduleList = self.globals['schedules'][trvCtlrDevId]['dynamic'].copy()

            trvcDev = indigo.devices[trvCtlrDevId]

            initialiseHeatingSheduleLog = u'\n\n{}'.format('|'*80)
            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n||  Device: {}\n||  Method: processAdvance [BEFORE]'.format(indigo.devices[trvCtlrDevId].name)
            for key, value in scheduleList.items():
                scheduleTime = int(key)
                scheduleTimeUi = u'{}'.format(value[0])
                scheduleSetpoint = float(value[1])
                scheduleId = int(value[2])
                scheduleActive = bool(value[3])
                initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n||  Time = {}, Setpoint = {}, Id = {}'.format(scheduleTimeUi, scheduleSetpoint, scheduleId)
            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n||  ScheduleList Length = {}, ScheduleList Type = {}'.format(len(scheduleList), type(scheduleList))
            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n{}\n\n'.format('||'*80)
            self.trvHandlerLogger.debug(initialiseHeatingSheduleLog)


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
                if key > ct and key < scheduleKeyNext:
                    del scheduleList[key]

            initialiseHeatingSheduleLog = u'\n\n{}'.format('|'*80)
            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n||  Device: {}\n||  Method: processAdvance [AFTER]'.format(indigo.devices[trvCtlrDevId].name)
            for key, value in scheduleList.items():
                scheduleTime = int(key)
                scheduleTimeUi = u'{}'.format(value[0])
                scheduleSetpoint = float(value[1])
                scheduleId = int(value[2])
                scheduleActive = bool(value[3])
                initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n||  Time = {}, Setpoint = {}, Id = {}'.format(scheduleTimeUi, scheduleSetpoint, scheduleId)
            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n||\n|| Type={}, CT={}, Prev={}, Next={}'.format(ADVANCE_TRANSLATION[advanceType], ct, scheduleKeyPrevious, scheduleKeyNext)

            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n||  ScheduleList Length = {}, ScheduleList Type = {}'.format(len(scheduleList), type(scheduleList))
            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n{}\n\n'.format('||'*80)
            self.trvHandlerLogger.debug(initialiseHeatingSheduleLog)

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

            if nextSchedule == 240000:
                self.trvHandlerLogger.info(u'TRV Controller \'{}\' - No further schedule to \'Advance\' to - Advance not actioned!'.format(trvcDev.name))
                return

            ctTemp = '0{}'.format(ct)[-6:]  # e.g 91045 > 091045
            ctUi = '{}:{}'.format(ctTemp[0:2], ctTemp[2:4]) # e.g. 09:10 

            schedule = scheduleList[nextSchedule]
            scheduleTimeUi = u'{}'.format(schedule[0])
            scheduleSetpoint = float(schedule[1])
            scheduleId = int(schedule[2])
            scheduleActive = bool(schedule[3])
            scheduleActiveUi = 'Start' if scheduleActive else 'End'

            del scheduleList[nextSchedule]

            scheduleList[ct] = (ctUi, scheduleSetpoint, scheduleId, scheduleActive)

            self.globals['schedules'][trvCtlrDevId]['dynamic'] = collections.OrderedDict(sorted(scheduleList.items())).copy()

            self.trvHandlerLogger.debug(u'processAdvance [2]:\nRunning:\n{}\n\nDynamic:\n{}\n\n'.format(self.globals['schedules'][trvCtlrDevId]['running'], self.globals['schedules'][trvCtlrDevId]['dynamic']))
            

            self.globals['trvc'][trvCtlrDevId]['advanceActive'] = True
            self.globals['trvc'][trvCtlrDevId]['advanceStatusUi'] = 'Advanced to S{} \'{} at {}\' at {}'.format(scheduleId,  scheduleActiveUi, scheduleTimeUi, ctUi)
            self.globals['trvc'][trvCtlrDevId]['advanceActivatedTime'] = ctUi
            self.globals['trvc'][trvCtlrDevId]['advanceToScheduleTime'] = scheduleTimeUi

            keyValueList = [
                    {'key': 'advanceActive', 'value': self.globals['trvc'][trvCtlrDevId]['advanceActive']},
                    {'key': 'advanceStatusUi', 'value': self.globals['trvc'][trvCtlrDevId]['advanceStatusUi']},
                    {'key': 'advanceActivatedTime', 'value': self.globals['trvc'][trvCtlrDevId]['advanceActivatedTime']},
                    {'key': 'advanceToScheduleTime', 'value': self.globals['trvc'][trvCtlrDevId]['advanceToScheduleTime']}
                ]
            indigo.devices[trvCtlrDevId].updateStatesOnServer(keyValueList)

            self.trvHandlerLogger.info(u'TRV Controller \'{}\' - {}'.format(trvcDev.name, self.globals['trvc'][trvCtlrDevId]['advanceStatusUi']))

            # Set Timer to cancel advance when next schedule time reached
            secondsToNextSchedule, calcSecondsLog = calcSeconds(nextSchedule, ct)

            self.trvHandlerLogger.debug(u'processAdvance [3]: Seconds To Next Schedule = \'{}\'\n{}'.format(secondsToNextSchedule, calcSecondsLog))

            self.globals['timers']['advanceCancel'][trvCtlrDevId] = threading.Timer(float(secondsToNextSchedule), self.processAdvanceCancel, [trvCtlrDevId, False])
            self.globals['timers']['advanceCancel'][trvCtlrDevId].setDaemon(True)
            self.globals['timers']['advanceCancel'][trvCtlrDevId].start()

            self.processHeatingSchedule(trvCtlrDevId)

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in \'processAdvance\'. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def processAdvanceCancel(self, trvCtlrDevId, invokeProcessHeatingSchedule):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            if self.globals['trvc'][trvCtlrDevId]['advanceActive']:

                if trvCtlrDevId in self.globals['timers']['advanceCancel']:  # Cancel any existing advance cancel timer
                    self.globals['timers']['advanceCancel'][trvCtlrDevId].cancel()

                self.trvHandlerLogger.debug(u'processAdvanceCancel [1]:\nRunning:\n{}\n\nDynamic:\n{}\n\n'.format(self.globals['schedules'][trvCtlrDevId]['running'], self.globals['schedules'][trvCtlrDevId]['dynamic']))
                
                self.globals['schedules'][trvCtlrDevId]['dynamic'] = collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['running'].items())).copy()  # Reset Schedule to previous running state

                self.trvHandlerLogger.debug(u'processAdvanceCancel [2]:\nRunning:\n{}\n\nDynamic:\n{}\n\n'.format(self.globals['schedules'][trvCtlrDevId]['running'], self.globals['schedules'][trvCtlrDevId]['dynamic']))

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

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in \'processAdvanceCancel\'. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def processBoost(self, trvCtlrDevId, boostMode, boostDeltaT, boostSetpoint, boostMinutes):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            self.trvHandlerLogger.debug(u'Boost invoked for Thermostat \'{}\': DeltaT = \'{}\', Minutes = \'{}\''.format(indigo.devices[trvCtlrDevId].name, boostDeltaT, boostMinutes)) 

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
            endTime = startTime + datetime.timedelta(minutes= self.globals['trvc'][trvCtlrDevId]['boostMinutes'])

            self.globals['trvc'][trvCtlrDevId]['boostTimeStart'] = startTime.strftime('%H:%M')
            self.globals['trvc'][trvCtlrDevId]['boostTimeEnd'] = endTime.strftime('%H:%M')
            self.globals['trvc'][trvCtlrDevId]['boostStatusUi'] = '{} - {}'.format(self.globals['trvc'][trvCtlrDevId]['boostTimeStart'], self.globals['trvc'][trvCtlrDevId]['boostTimeEnd'])

            if self.globals['trvc'][trvCtlrDevId]['boostMode'] == BOOST_MODE_DELTA_T:
                self.globals['trvc'][trvCtlrDevId]['boostStatusUi'] = '{} [DeltaT = +{}]'.format(self.globals['trvc'][trvCtlrDevId]['boostStatusUi'], self.globals['trvc'][trvCtlrDevId]['boostDeltaT']) 
                newSetpoint = float(self.globals['trvc'][trvCtlrDevId]['temperature']) + float(boostDeltaT)
                if newSetpoint > float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum']):
                    newSetpoint = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum'])
            else:  # BOOST_MODE_SETPOINT
                newSetpoint = float(self.globals['trvc'][trvCtlrDevId]['boostSetpoint'])                
                self.globals['trvc'][trvCtlrDevId]['boostStatusUi'] = '{} [Setpoint = {}]'.format(self.globals['trvc'][trvCtlrDevId]['boostStatusUi'], newSetpoint) 

            keyValueList = [
            {'key': 'boostActive', 'value': bool(self.globals['trvc'][trvCtlrDevId]['boostActive'])},
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
            {'key': 'setpointHeat', 'value': newSetpoint}
                ]
            indigo.devices[trvCtlrDevId].updateStatesOnServer(keyValueList)

            self.globals['timers']['boost'][trvCtlrDevId] = threading.Timer(float(boostMinutes * 60), self.boostCancelTriggered, [trvCtlrDevId, True])
            self.globals['timers']['boost'][trvCtlrDevId].setDaemon(True)
            self.globals['timers']['boost'][trvCtlrDevId].start()

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [processBoost]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   


    def processBoostCancel(self, trvCtlrDevId, invokeProcessHeatingSchedule):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            self.boostCancelTriggered(trvCtlrDevId, invokeProcessHeatingSchedule)
            
        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [boostCancelTriggered]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def boostCancelTriggered(self, trvCtlrDevId, invokeProcessHeatingSchedule):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            if self.globals['trvc'][trvCtlrDevId]['boostActive']:

                if trvCtlrDevId in self.globals['timers']['boost']:
                    self.globals['timers']['boost'][trvCtlrDevId].cancel()
                    self.trvHandlerLogger.debug(u'boostCancelTriggered timer cancelled for device \'{}\''.format(indigo.devices[trvCtlrDevId].name))

                self.trvHandlerLogger.debug(u'Boost CANCEL processed for Thermostat \'{}\''.format(indigo.devices[trvCtlrDevId].name)) 

                self.globals['trvc'][trvCtlrDevId]['boostActive'] = False
                self.globals['trvc'][trvCtlrDevId]['boostMode'] = BOOST_MODE_INACTIVE
                self.globals['trvc'][trvCtlrDevId]['boostModeUi'] = BOOST_MODE_TRANSLATION[self.globals['trvc'][trvCtlrDevId]['boostMode']]
                self.globals['trvc'][trvCtlrDevId]['boostDeltaT'] = float(0.0)
                self.globals['trvc'][trvCtlrDevId]['boostSetpoint'] = float(0.0)
                self.globals['trvc'][trvCtlrDevId]['boostMinutes'] = int(0)
                self.globals['trvc'][trvCtlrDevId]['boostTimeStart'] = 'Inactive'
                self.globals['trvc'][trvCtlrDevId]['boostTimeEnd'] = 'Inactive'
                self.globals['trvc'][trvCtlrDevId]['boostStatusUi'] = ''

                keyValueList = [
                {'key': 'boostActive', 'value': bool(self.globals['trvc'][trvCtlrDevId]['boostActive'])},
                {'key': 'boostMode', 'value': int(self.globals['trvc'][trvCtlrDevId]['boostMode'])},
                {'key': 'boostModeUi', 'value': self.globals['trvc'][trvCtlrDevId]['boostModeUi']},
                {'key': 'boostStatusUi', 'value': self.globals['trvc'][trvCtlrDevId]['boostStatusUi']},
                {'key': 'boostDeltaT', 'value': float(self.globals['trvc'][trvCtlrDevId]['boostDeltaT'])},
                {'key': 'boostSetpoint', 'value': int(self.globals['trvc'][trvCtlrDevId]['boostSetpoint'])},
                {'key': 'boostMinutes', 'value': int(self.globals['trvc'][trvCtlrDevId]['boostMinutes'])},
                {'key': 'boostTimeStart', 'value': self.globals['trvc'][trvCtlrDevId]['boostTimeStart']},
                {'key': 'boostTimeEnd', 'value': self.globals['trvc'][trvCtlrDevId]['boostTimeEnd']}
                    ]
                indigo.devices[trvCtlrDevId].updateStatesOnServer(keyValueList)

                if invokeProcessHeatingSchedule:
                    self.globals['trvc'][trvCtlrDevId]['boostSetpointInvokeRestore'] = True
                    self.processHeatingSchedule(trvCtlrDevId)

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [boostCancelTriggered]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def processExtend(self, trvCtlrDevId, extendIncrementMinutes, extendMaximumMinutes):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            self.trvHandlerLogger.debug(u'Extend processed for Thermostat \'{}\': Increment Minutes = \'{}\', Maximum Minutes = \'{}\''.format(indigo.devices[trvCtlrDevId].name, extendIncrementMinutes, extendMaximumMinutes)) 

            self.processAdvanceCancel(trvCtlrDevId, False)
            self.processBoostCancel(trvCtlrDevId, False)

            if trvCtlrDevId in self.globals['timers']['heatingSchedules']:
                self.globals['timers']['heatingSchedules'][trvCtlrDevId].cancel()

            extendMinutes = self.globals['trvc'][trvCtlrDevId]['extendMinutes'] + extendIncrementMinutes
            if (extendMinutes > extendMaximumMinutes) or self.globals['trvc'][trvCtlrDevId]['extendLimitReached']:
                self.processExtendCancel(trvCtlrDevId, True)
                return

            self.trvHandlerLogger.debug(u'processAdvance [0]:\nRunning:\n{}\n\nDynamic:\n{}\n\n'.format(self.globals['schedules'][trvCtlrDevId]['running'], self.globals['schedules'][trvCtlrDevId]['dynamic']))

            scheduleList = self.globals['schedules'][trvCtlrDevId]['running'].copy()

            trvcDev = indigo.devices[trvCtlrDevId]

            initialiseHeatingSheduleLog = u'\n\n{}'.format('|'*80)
            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n||  Device: {}\n||  Method: processExtend'.format(indigo.devices[trvCtlrDevId].name)
            for key, value in scheduleList.items():
                scheduleTime = int(key)
                scheduleTimeUi = u'{}'.format(value[SCHEDULE_TIME_UI])
                scheduleSetpoint = float(value[SCHEDULE_SETPOINT])
                scheduleId = int(value[SCHEDULE_ID])
                scheduleActive = bool(value[SCHEDULE_ACTIVE])
                initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n||  Time = {}, Setpoint = {}, Id = {}'.format(scheduleTimeUi, scheduleSetpoint, scheduleId)
            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n||  ScheduleList Length = {}, ScheduleList Type = {}'.format(len(scheduleList), type(scheduleList))

            ct = int(datetime.datetime.now().strftime('%H%M%S'))

            def calcExtension(nextSchedule, nextSchedulePlusOne, extendMinutes):

                def evalExtensionSeconds(et): # e.g.: 141545
                    etHH = et / 10000  # e.g.: 14
                    etTemp = et % 10000 # e.g. 1545
                    etMM = etTemp / 100 # e.g.: 15
                    etSS = etTemp % 100  # e.g.: 45
                    etSeconds = (etHH * 3600) + (etMM * 60) + etSS
                    return etSeconds

                nextScheduleSeconds = evalExtensionSeconds(nextSchedule)
                nextSchedulePlusOneSeconds = evalExtensionSeconds(nextSchedulePlusOne)
                nextScheduleTimeLimitSeconds = nextSchedulePlusOneSeconds - 300  # Minus 5 minutes

                limitFlag = False

                extendedScheduleSeconds = nextScheduleSeconds + (extendMinutes * 60)

                if extendedScheduleSeconds > nextScheduleTimeLimitSeconds:
                    extendedScheduleSeconds = nextScheduleTimeLimitSeconds
                    limitFlag = True

                extendedScheduleTimeHH = extendedScheduleSeconds / 3600
                extendedScheduleTimeTemp = extendedScheduleSeconds % 3600
                extendedScheduleTimeMM = extendedScheduleTimeTemp / 60
                extendedScheduleTimeSS = extendedScheduleTimeTemp % 60
                extendedScheduleTime = (extendedScheduleTimeHH * 10000) + (extendedScheduleTimeMM * 100) + extendedScheduleTimeSS

                return int(extendedScheduleTime), bool(limitFlag)

            currentScheduleTime = max(k for k in scheduleList if k <= ct)
            currentSchedule = scheduleList[currentScheduleTime]
            currentScheduleActiveUi = 'Start' if bool(currentSchedule[SCHEDULE_ACTIVE]) else 'End'
            currentScheduleId = int(currentSchedule[SCHEDULE_ID])


            originalNextScheduleTime = min(k for k in scheduleList if k >= ct)
            if originalNextScheduleTime == 240000:
                self.trvHandlerLogger.info(u'Extend request for \'{}\' ignored; Can\'t  Extend beyond end-of-day (24:00)'.format(indigo.devices[trvCtlrDevId].name))
                return
            else:
                nextSchedulePlusOne =  min(k for k in scheduleList if k > originalNextScheduleTime)
            extendedNextScheduleTime, self.globals['trvc'][trvCtlrDevId]['extendLimitReached'] = calcExtension(originalNextScheduleTime, nextSchedulePlusOne, extendMinutes)

            self.trvHandlerLogger.debug(u'processExtend: Original Next Schedule Time = \'{}\', Next Schedule Plus One Time =  \'{}\', Extend Minutes Reached = \'{}\', Extended Next Schedule Time = \'{}\', Extend Limit = {}'.format(originalNextScheduleTime, nextSchedulePlusOne, extendMinutes, extendedNextScheduleTime, self.globals['trvc'][trvCtlrDevId]['extendLimitReached']))            

            extendedPreviousScheduleTime = max(k for k in scheduleList if k <= extendedNextScheduleTime)

            originalNextSchedule = scheduleList[originalNextScheduleTime]
            extendedNextScheduleSetpoint = float(originalNextSchedule[SCHEDULE_SETPOINT])
            extendedNextScheduleScheduleId = int(originalNextSchedule[SCHEDULE_ID])
            extendedNextScheduleScheduleActive = bool(originalNextSchedule[SCHEDULE_ACTIVE])
            extendedNextScheduleScheduleActiveUi = 'Start' if extendedNextScheduleScheduleActive else 'End'

            originalNextScheduleTimeWork = '0{}'.format(originalNextScheduleTime)[-6:]
            originalNextScheduleTimeUi = '{}:{}'.format(originalNextScheduleTimeWork[0:2], originalNextScheduleTimeWork[2:4])

            extendedNextScheduleTimeWork = '0{}'.format(extendedNextScheduleTime)[-6:]
            extendedNextScheduleTimeUi = '{}:{}'.format(extendedNextScheduleTimeWork[0:2], extendedNextScheduleTimeWork[2:4])

            del scheduleList[originalNextScheduleTime]

            scheduleList[extendedNextScheduleTime] = (extendedNextScheduleTimeUi, extendedNextScheduleSetpoint, extendedNextScheduleScheduleId, extendedNextScheduleScheduleActive)

            self.globals['schedules'][trvCtlrDevId]['dynamic'] = collections.OrderedDict(sorted(scheduleList.items())).copy()

            self.trvHandlerLogger.debug(u'processExtend [1]:\nRunning:\n{}\n\nDynamic:\n{}\n\n'.format(self.globals['schedules'][trvCtlrDevId]['running'], self.globals['schedules'][trvCtlrDevId]['dynamic']))
            
            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n{}\n\n'.format('||'*80)
            self.trvHandlerLogger.debug(initialiseHeatingSheduleLog)

            self.globals['trvc'][trvCtlrDevId]['extendActive'] = True

            self.globals['trvc'][trvCtlrDevId]['extendIncrementMinutes'] = int(extendIncrementMinutes)
            self.globals['trvc'][trvCtlrDevId]['extendMinutes'] = int(extendMinutes)
            self.globals['trvc'][trvCtlrDevId]['extendMaximumMinutes'] = int(extendMaximumMinutes)

            startTime = datetime.datetime.now()

            self.globals['trvc'][trvCtlrDevId]['extendActivatedTime'] = startTime.strftime('%H:%M')
            self.globals['trvc'][trvCtlrDevId]['extendScheduleOriginalTime'] = originalNextScheduleTimeUi
            self.globals['trvc'][trvCtlrDevId]['extendScheduleNewTime'] = extendedNextScheduleTimeUi

            self.globals['trvc'][trvCtlrDevId]['extendStatusUi'] = 'S{} \'{} at {} => {}\' at {}'.format(extendedNextScheduleScheduleId, extendedNextScheduleScheduleActiveUi, self.globals['trvc'][trvCtlrDevId]['extendScheduleOriginalTime'], self.globals['trvc'][trvCtlrDevId]['extendScheduleNewTime'], self.globals['trvc'][trvCtlrDevId]['extendActivatedTime'])
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

            self.trvHandlerLogger.info(u'Extending current \'{}\' schedule for \'{}\': Next \'{}\' Schedule Time of \'{}\' altered to \'{}\''.format(currentScheduleActiveUi, indigo.devices[trvCtlrDevId].name, extendedNextScheduleScheduleActiveUi, self.globals['trvc'][trvCtlrDevId]['extendScheduleOriginalTime'], self.globals['trvc'][trvCtlrDevId]['extendScheduleNewTime']))

            self.processHeatingSchedule(trvCtlrDevId)

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in \'processExtend\'. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def processExtendCancel(self, trvCtlrDevId, invokeProcessHeatingSchedule):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            if self.globals['trvc'][trvCtlrDevId]['extendActive']:

                self.trvHandlerLogger.debug(u'processExtendCancel [1]:\nRunning:\n{}\n\nDynamic:\n{}\n\n'.format(self.globals['schedules'][trvCtlrDevId]['running'], self.globals['schedules'][trvCtlrDevId]['dynamic']))
                
                self.globals['schedules'][trvCtlrDevId]['dynamic'] = collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['running'].items())).copy()  # Reset Schedule to previous running state

                self.trvHandlerLogger.debug(u'processExtendCancel [2]:\nRunning:\n{}\n\nDynamic:\n{}\n\n'.format(self.globals['schedules'][trvCtlrDevId]['running'], self.globals['schedules'][trvCtlrDevId]['dynamic']))

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

                self.trvHandlerLogger.info(u'Extend schedule cancelled for \'{}\''.format(indigo.devices[trvCtlrDevId].name))

                if invokeProcessHeatingSchedule:
                    self.processHeatingSchedule(trvCtlrDevId)

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in \'processExtendCancel\'. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def resetScheduleToDeviceDefaults(self, trvCtlrDevId):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            trvcDev = indigo.devices[trvCtlrDevId]
            if trvcDev.enabled:
                self.trvHandlerLogger.info(u'Resetting schedules to default values for TRV Controller \'{}\''.format(trvcDev.name))
                indigo.device.enable(trvcDev.id, value=False) #disable
                time.sleep(5)
                indigo.device.enable(trvcDev.id, value=True) #enable

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [resetScheduleToDeviceDefaults]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def restateSchedules(self):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            for trvcDev in indigo.devices.iter('self'):
                if trvcDev.enabled:
                    self.trvHandlerLogger.info(u'Forcing restatement of schedules to default values for TRV Controller \'{}\''.format(trvcDev.name))
                    indigo.device.enable(trvcDev.id, value=False) #disable
                    time.sleep(5)
                    indigo.device.enable(trvcDev.id, value=True) #enable
                    time.sleep(2)

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [restateSchedules]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def processHeatingSchedule(self, trvCtlrDevId):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            schedulingEnabled = self.globals['trvc'][trvCtlrDevId]['schedule1Enabled'] or self.globals['trvc'][trvCtlrDevId]['schedule2Enabled'] or self.globals['trvc'][trvCtlrDevId]['schedule3Enabled'] or self.globals['trvc'][trvCtlrDevId]['schedule4Enabled']

            scheduleList =  collections.OrderedDict(sorted(self.globals['schedules'][trvCtlrDevId]['dynamic'].items())) 

            if trvCtlrDevId in self.globals['timers']['heatingSchedules']:
                self.globals['timers']['heatingSchedules'][trvCtlrDevId].cancel()

            trvcDev = indigo.devices[trvCtlrDevId]

            initialiseHeatingSheduleLog = u'\n\n{}'.format('@'*80)
            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n@@  Device: {}\n@@  Method: processHeatingSchedule'.format(indigo.devices[trvCtlrDevId].name)
            for key, value in scheduleList.items():
                scheduleTime = int(key)  # HHMMSS
                scheduleTimeUi = u'{}'.format(value[0])  # 'HH:MM'
                scheduleSetpoint = float(value[1])
                scheduleId = value[2]

                initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n@@  Time = {}, Setpoint = {}, Id = {}'.format(scheduleTimeUi, scheduleSetpoint, scheduleId)

            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n@@  ScheduleList Length = {}, ScheduleList Type = {}'.format(len(scheduleList), type(scheduleList))

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

            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n@@\n@@  CT={}, Prev={}, Next={}'.format(ct, previousSchedule, nextSchedule)


            schedule1Active = False
            schedule2Active = False
            schedule3Active = False
            schedule4Active = False

            if nextSchedule < 240000:
                if previousSchedule == 0:  # i.e. start of day
                    schedule = scheduleList[previousSchedule]

                    initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n@@  Current Time = {}, No schedule active'.format(ct)

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
                    self.trvHandlerLogger.debug(u'processHeatingSchedule: Adjusting TRV Controller \'{}\' Setpoint Heat to {}'.format(trvcDev.name, self.globals['trvc'][trvCtlrDevId]['setpointHeat']))

                else:
                    schedule = scheduleList[previousSchedule]

                    if schedule[SCHEDULE_ACTIVE]:
                        initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n@@  Current Time = {}, Current Schedule started at {} = {}'.format(ct, previousSchedule, schedule) 
                        if schedule[SCHEDULE_ID] == 1:
                            schedule1Active = True
                        elif schedule[SCHEDULE_ID] == 2:
                            schedule2Active = True
                        elif schedule[SCHEDULE_ID] == 3:
                            schedule3Active = True
                        elif schedule[SCHEDULE_ID] == 4:
                            schedule4Active = True
                    else:
                        initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n@@  Current Time = {}, Last Schedule finished at {} = {}'.format(ct, previousSchedule, schedule)

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
                    self.trvHandlerLogger.debug(u'processHeatingSchedule: Adjusting TRV Controller \'{}\' Setpoint Heat to {}'.format(trvcDev.name, self.globals['trvc'][trvCtlrDevId]['setpointHeat']))

                schedule = scheduleList[nextSchedule]
                initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n@@  Next Schedule starts at {} = {}'.format(nextSchedule, schedule)

                secondsToNextSchedule, calcSecondsLog = calcSeconds(nextSchedule, ct) 
                initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n@@  calcSeconds: {}'.format(calcSecondsLog)

                self.trvHandlerLogger.debug(u'processHeatingSchedule: CALCSECONDS [{}] =  \'{}\''.format(type(secondsToNextSchedule), secondsToNextSchedule))


                self.globals['timers']['heatingSchedules'][trvCtlrDevId] = threading.Timer(float(secondsToNextSchedule), self.heatingScheduleTriggered, [trvCtlrDevId])
                self.globals['timers']['heatingSchedules'][trvCtlrDevId].setDaemon(True)
                self.globals['timers']['heatingSchedules'][trvCtlrDevId].start()

                nsetTemp = '0{}'.format(nextSchedule)[-6:]  # e.g 91045 > 091045
                nsetUi = '{}:{}'.format(nsetTemp[0:2], nsetTemp[2:4]) # e.g. 09:10 

                self.globals['trvc'][trvCtlrDevId]['nextScheduleExecutionTime'] = nsetUi
                trvcDev.updateStateOnServer(key='nextScheduleExecutionTime', value=self.globals['trvc'][trvCtlrDevId]['nextScheduleExecutionTime'])                    

            else:

                if schedulingEnabled:
                    schedule = scheduleList[nextSchedule]
                    self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(schedule[SCHEDULE_SETPOINT])
                    self.trvHandlerLogger.debug(u'processHeatingSchedule: Adjusting TRV Controller \'{}\' Setpoint Heat to {}'.format(trvcDev.name, self.globals['trvc'][trvCtlrDevId]['setpointHeat']))
                else:
                    if self.globals['trvc'][trvCtlrDevId]['boostSetpointInvokeRestore']:
                        self.globals['trvc'][trvCtlrDevId]['boostSetpointInvokeRestore'] = False
                        self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = self.globals['trvc'][trvCtlrDevId]['boostSetpointToRestore']
                        self.globals['trvc'][trvCtlrDevId]['boostSetpointToRestore'] = 0.0
                        self.trvHandlerLogger.debug(u'processHeatingSchedule: Restoring TRV Controller \'{}\' Setpoint to pre-boost value {}'.format(trvcDev.name, self.globals['trvc'][trvCtlrDevId]['setpointHeat']))
                    else:
                        self.trvHandlerLogger.debug(u'processHeatingSchedule: Leaving TRV Controller \'{}\' Setpoint at {}'.format(trvcDev.name, self.globals['trvc'][trvCtlrDevId]['setpointHeat']))

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

                initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n@@  Current Time = {}, No schedule active or pending'.format(ct)


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
                        self.pollSpiritTriggered(trvCtlrDevId, pollingSeconds)  # Initiate polling sequence and force immediate status update

            initialiseHeatingSheduleLog = initialiseHeatingSheduleLog + u'\n{}\n\n'.format('@'*80)
            self.trvHandlerLogger.debug(initialiseHeatingSheduleLog)

            self.controlTrv(trvCtlrDevId)

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in \'processHeatingSchedule\'. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def pollSpiritTriggered(self, trvCtlrDevId, pollingSeconds):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            if trvCtlrDevId in self.globals['timers']['SpiritPolling']:                            
                self.globals['timers']['SpiritPolling'][trvCtlrDevId].cancel()

            self.globals['trvc'][trvCtlrDevId]['pollingSeconds'] = float(pollingSeconds)

            trvDevId = self.globals['trvc'][trvCtlrDevId]['trvDevId']
            valveDevId = self.globals['trvc'][trvCtlrDevId]['valveDevId']

            indigo.device.statusRequest(trvDevId)  # Request Spirit Thermostat status

            if valveDevId != 0:
                indigo.device.statusRequest(valveDevId)  # Request Spirit Valve status

            self.globals['timers']['SpiritPolling'][trvCtlrDevId] = threading.Timer(pollingSeconds, self.pollSpiritTriggered, [trvCtlrDevId, pollingSeconds])  # Initiate next poll
            self.globals['timers']['SpiritPolling'][trvCtlrDevId].setDaemon(True)
            self.globals['timers']['SpiritPolling'][trvCtlrDevId].start()
                
            self.trvHandlerLogger.debug(u'pollSpiritTriggered: Polling \'{}\' Spirit Thermostat every {} seconds.'.format(indigo.devices[trvDevId].name, int(pollingSeconds)))

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in \'pollSpiritTriggered\'. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   


    def heatingScheduleTriggered(self, trvCtlrDevId):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            # self.trvHandlerLogger.info(u'Schedule Change Triggered for \'{}\' - Will activate in one minute'.format(indigo.devices[trvCtlrDevId].name))
        
            # self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_DELAY_COMMAND, trvCtlrDevId, [CMD_PROCESS_HEATING_SCHEDULE, 60, None]])            

            time.sleep(2)  # wait 2 seconds 
            self.trvHandlerLogger.info(u'Schedule Change Triggered for \'{}\''.format(indigo.devices[trvCtlrDevId].name))

            self.processExtendCancel(trvCtlrDevId, False)

            self.processHeatingSchedule(trvCtlrDevId)

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in \'heatingScheduleTriggered\'. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def delayCommand(self, trvDelayedCommand, trvCtlrDevId, trvDelayedSeconds, trvDelayedCommandPackage):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            self.globals['timers']['command'][trvCtlrDevId] = threading.Timer(float(trvDelayedSeconds), self.delayCommandTimerTriggered, [trvDelayedCommand, trvCtlrDevId, trvDelayedCommandPackage])  # 3,300 seconds = 55 minutes :)
            self.globals['timers']['command'][trvCtlrDevId].setDaemon(True)
            self.globals['timers']['command'][trvCtlrDevId].start()

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in \'heatingScheduleTriggered\'. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def delayCommandTimerTriggered(self, trvDelayedCommand, trvCtlrDevId, trvDelayedCommandPackage):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, trvDelayedCommand, trvCtlrDevId, trvDelayedCommandPackage])

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in \'delayCommandTimerTriggered\'. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def controlHeatingSource(self, trvCtlrDevId, heatingId, heatingVarId):

        self.methodTracer.threaddebug(u'TrvHandler Method')

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
                        callingForHeatUi = callingForHeatUi + '  > {}\n'.format(indigo.devices[callingForHeatTrvCtlrDevId].name)

                self.trvHandlerLogger.debug(u'Control Heating Source: Thermostats calling for heat from Device \'{}\': {}'.format(indigo.devices[heatingId].name, callingForHeatUi))
                if len(self.globals['heaterDevices'][heatingId]['thermostatsCallingForHeat']) > 0:
                    # if there are thermostats calling for heat, the heating needs to be 'on'
                    #indigo.variable.updateValue(self.variableId, value="true")  # Variable indicator to show that heating is being requested
                    if self.globals['heaterDevices'][heatingId]['onState'] != HEAT_SOURCE_ON:
                        self.globals['heaterDevices'][heatingId]['onState'] = HEAT_SOURCE_ON 
                        if self.globals['heaterDevices'][heatingId]['heaterControlType'] == HEAT_SOURCE_CONTROL_HVAC:
                            if indigo.devices[heatingId].states['hvacOperationMode'] != HVAC_HEAT:
                                indigo.thermostat.setHvacMode(heatingId, value=HVAC_HEAT) # Turn heating 'on'
                        elif self.globals['heaterDevices'][heatingId]['heaterControlType'] == HEAT_SOURCE_CONTROL_RELAY:
                            if not indigo.devices[heatingId].onState:
                                indigo.device.turnOn(heatingId) # Turn heating 'on'
                        else:
                            pass  # ERROR SITUATION
                else:
                    # if no thermostats are calling for heat, then the heating needs to be 'off'
                    # indigo.variable.updateValue(self.variableId, value="false")  # Variable indicator to show that heating is NOT being requested
                    if  self.globals['heaterDevices'][heatingId]['onState'] != HEAT_SOURCE_OFF:
                        self.globals['heaterDevices'][heatingId]['onState'] = HEAT_SOURCE_OFF 
                        if self.globals['heaterDevices'][heatingId]['heaterControlType'] == HEAT_SOURCE_CONTROL_HVAC:
                            if indigo.devices[heatingId].states['hvacOperationMode'] != HVAC_OFF:
                                indigo.thermostat.setHvacMode(heatingId, value=HVAC_OFF) # Turn heating 'off'
                        elif self.globals['heaterDevices'][heatingId]['heaterControlType'] == HEAT_SOURCE_CONTROL_RELAY:        
                            if indigo.devices[heatingId].onState:
                                indigo.device.turnOff(heatingId) # Turn heating 'off'
                        else:
                            pass  # ERROR SITUATION
  
            if heatingVarId != 0:
                if len(self.globals['heaterVariables'][heatingVarId]['thermostatsCallingForHeat']) == 0:
                    callingForHeatUi = 'None'
                else:
                    callingForHeatUi = '\n'
                    for callingForHeatTrvCtlrDevId in self.globals['heaterVariables'][heatingVarId]['thermostatsCallingForHeat']:
                        callingForHeatUi = callingForHeatUi + '  > {}\n'.format(indigo.devices[callingForHeatTrvCtlrDevId].name)

                self.trvHandlerLogger.debug(u'Control Heating Source: Thermostats calling for heat from Variable \'{}\': {}'.format(indigo.variables[heatingVarId].name, callingForHeatUi))
                if len(self.globals['heaterVariables'][heatingVarId]['thermostatsCallingForHeat']) > 0:
                    # if there are thermostats calling for heat, the heating needs to be 'on'
                    indigo.variable.updateValue(heatingVarId, value="true")  # Variable indicator to show that heating is being requested
                else:
                    # if no thermostats are calling for heat, then the heating needs to be 'off'
                    indigo.variable.updateValue(heatingVarId, value="false")  # Variable indicator to show that heating is NOT being requested
  
        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [controlHeatingSource]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   
        finally:
            self.globals['lock'].release()
 
    def keepHeatSourceControllerAlive(self, heatingId):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            # Only needed for SSR302 / SSR303 - needs updating every 55 minutes
            if indigo.devices[heatingId].model == "1 Channel Boiler Actuator (SSR303 / ASR-ZW)" or indigo.devices[heatingId].model ==  "2 Channel Boiler Actuator (SSR302)":

                self.globals['lock'].acquire()
                try:
                    # if there are thermostats calling for heat, the heating needs to be 'on'
                    if len(self.globals['heaterDevices'][heatingId]['thermostatsCallingForHeat']) > 0:
                        indigo.thermostat.setHvacMode(heatingId, value=indigo.kHvacMode.Heat) # remind Heat Source Controller to stay 'on'
                    else:
                        indigo.thermostat.setHvacMode(heatingId, value=indigo.kHvacMode.Off) # remind Heat Source Controller to stay 'off'
                finally:
                    self.globals['lock'].release()

                self.globals['timers']['heaters'][heatingId] = threading.Timer(3300.0, self.keepHeatSourceControllerAliveTimerTriggered, [heatingId])  # 3,300 seconds = 55 minutes :)
                self.globals['timers']['heaters'][heatingId].setDaemon(True)
                self.globals['timers']['heaters'][heatingId].start()

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [keepHeatSourceControllerAlive]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   


    def keepHeatSourceControllerAliveTimerTriggered(self, heatingId):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_KEEP_HEAT_SOURCE_CONTROLLER_ALIVE, None, [heatingId, ]])

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [keepHeatSourceControllerAliveTimerTriggered]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))

    def updateDeviceStates(self, trvCtlrDevId, command, updateList, sequence):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            if indigo.devices[int(self.globals['trvc'][trvCtlrDevId]['trvDevId'])].enabled is True:

                dev = indigo.devices[trvCtlrDevId]

                updateDeviceStatesLog = u'\n\nXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX'
                updateDeviceStatesLog = updateDeviceStatesLog + u'\nXX  Method: \'updateDeviceStates\''
                updateDeviceStatesLog = updateDeviceStatesLog + u'\nXX  Sequence: {}'.format(sequence)
                updateDeviceStatesLog = updateDeviceStatesLog + u'\nXX  Device: TRV CONTROLLER - \'{}\''.format(indigo.devices[trvCtlrDevId].name)

                updateDeviceStatesLog = updateDeviceStatesLog + u'\nXX  List of states to be updated:'
                for itemToUpdate in updateList.iteritems():
                    updateKey = itemToUpdate[0]
                    updateValue = itemToUpdate[1]
                    # updateInfo = updateInfo + 'Key = {}, Description = {}, Value = {}\n'.format(updateKey, UPDATE_TRANSLATION[updateKey], updateValue)
                    updateDeviceStatesLog = updateDeviceStatesLog + '\nXX    > Description = {}, Value = {}'.format(UPDATE_TRANSLATION[updateKey], updateValue)

                updateKeyValueList = []

                # YET TO BE DONE ...
                #   - UPDATE_ZWAVE_HVAC_OPERATION_MODE_ID = 4
                #   - UPDATE_ZWAVE_WAKEUP_INTERVAL = 6

                for itemToUpdate in updateList.iteritems():
                    updateKey = itemToUpdate[0]
                    updateValue = itemToUpdate[1]

                    if updateKey == UPDATE_CONTROLLER_HVAC_OPERATION_MODE: 
                        self.globals['trvc'][trvCtlrDevId]['hvacOperationMode'] = int(updateValue)
                        if dev.states['hvacOperationMode'] != int(updateValue):
                            updateKeyValueList.append({'key': 'hvacOperationMode', 'value':  int(updateValue)})

                    # if updateKey == UPDATE_CONTROLLER_TEMPERATURE: 
                    #     self.globals['trvc'][trvCtlrDevId]['temperature'] = float(updateValue)

                    if updateKey == UPDATE_CONTROLLER_HEAT_SETPOINT: 
                        self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(updateValue)
                        # if dev.heatSetpoint != float(updateValue):
                            # updateKeyValueList.append({'key': 'setpointHeat', 'value': float(updateValue)})  # Not needed 

                    if updateKey == UPDATE_CONTROLLER_MODE:
                        self.globals['trvc'][trvCtlrDevId]['controllerMode'] = int(updateValue)
                        if dev.states['controllerMode'] != int(updateValue):
                            updateKeyValueList.append({'key': 'controllerMode', 'value':  int(updateValue)})
                            updateKeyValueList.append({'key': 'controllerModeUi', 'value':  CONTROLLER_MODE_TRANSLATION[int(updateValue)]})

                    elif updateKey == UPDATE_TRV_BATTERY_LEVEL:
                        self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'] = int(updateValue)
                        if dev.states['batteryLevelTrv'] != int(updateValue):
                            updateKeyValueList.append({'key': 'batteryLevelTrv', 'value':  int(updateValue)})
                        if self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'] != 0 and self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'] < self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote']:
                            if dev.states['batteryLevel'] != int(updateValue):
                                updateKeyValueList.append({'key': 'batteryLevel', 'value':  int(updateValue)})

                    elif updateKey == UPDATE_TRV_TEMPERATURE:  
                        self.globals['trvc'][trvCtlrDevId]['temperatureTrv'] = float(updateValue)
                        if dev.states['temperatureTrv'] != float(updateValue):
                            updateKeyValueList.append({'key': 'temperatureTrv', 'value':  float(updateValue)})
                            if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] == 0:
                                updateKeyValueList.append({'key': 'temperatureInput1', 'value': float(updateValue), 'uiValue': '{:.1f} °C'.format(float(updateValue))})
                                self.globals['trvc'][trvCtlrDevId]['temperature'] = float(updateValue)
                                updateKeyValueList.append({'key': 'temperature', 'value':  float(updateValue)})
                                updateKeyValueList.append({'key': 'temperatureUi', 'value': '{:.1f} °C'.format(float(updateValue))})
                            else:
                                updateKeyValueList.append({'key': 'temperatureInput2', 'value': float(updateValue), 'uiValue': '{:.1f} °C'.format(float(updateValue))})
                                updateKeyValueList.append({'key': 'temperatureUi', 'value': 'R: {:.1f} °C, T: {:.1f} °C'.format(self.globals['trvc'][trvCtlrDevId]['temperatureRemote'], float(updateValue))})

                    elif updateKey == UPDATE_TRV_HVAC_OPERATION_MODE:
                        if dev.states['hvacOperationModeTrv'] != int(updateValue):
                            if int(updateValue) == RESET_TO_HVAC_HEAT:
                                updateValue = HVAC_HEAT
                                indigo.thermostat.setHvacMode(self.globals['trvc'][trvCtlrDevId]['trvDevId'], value=indigo.kHvacMode.Heat)  # Force reset on TRV device
                            updateKeyValueList.append({'key': 'hvacOperationModeTrv', 'value':  int(updateValue)})
                        self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'] = int(updateValue)

                        # NEXT BIT OF LOGIC NEEDS SOME ENHANCEMENT

                        if self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'] == HVAC_OFF:
                            self.globals['trvc'][trvCtlrDevId]['hvacOperationMode'] = HVAC_OFF
                            if dev.states['hvacOperationMode'] != int(HVAC_OFF):
                                updateKeyValueList.append({'key': 'hvacOperationMode', 'value':  int(HVAC_OFF)})
                        elif self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'] == HVAC_HEAT:  
                            self.globals['trvc'][trvCtlrDevId]['hvacOperationMode'] = HVAC_HEAT
                            if dev.states['hvacOperationMode'] != int(HVAC_HEAT):
                                updateKeyValueList.append({'key': 'hvacOperationMode', 'value':  int(HVAC_HEAT)})

                    elif updateKey == UPDATE_TRV_HEAT_SETPOINT:
                        self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'] = float(updateValue)
                        if dev.states['setpointHeatTrv'] != float(updateValue):
                            updateKeyValueList.append({'key': 'setpointHeatTrv', 'value':  float(updateValue)})
                        # if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] == 0 and self.globals['trvc'][trvCtlrDevId]['controllerMode'] != CONTROLLER_MODE_UI:
                        #     self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(updateValue)  # <============================================================================== Delta T processing needed ???
                        #     if dev.states['setpointHeat'] != float(updateValue):
                        #         updateKeyValueList.append({'key': 'setpointHeat', 'value':  float(updateValue)})

                    elif updateKey == UPDATE_TRV_HEAT_SETPOINT_FROM_DEVICE:
                        self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'] = float(updateValue)
                        if dev.states['setpointHeatTrv'] != float(updateValue):
                            updateKeyValueList.append({'key': 'setpointHeatTrv', 'value':  float(updateValue)})
                        self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(updateValue)
                        if dev.states['setpointHeat'] != float(updateValue):
                            updateKeyValueList.append({'key': 'setpointHeat', 'value':  float(updateValue)})
                        if self.globals['trvc'][trvCtlrDevId]['remoteDevId'] != 0 and self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl']:
                            self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote'] = float(updateValue)
                            if dev.states['setpointHeatRemote'] != float(updateValue):
                                updateKeyValueList.append({'key': 'setpointHeatRemote', 'value':  float(updateValue)})


                    elif updateKey == UPDATE_REMOTE_BATTERY_LEVEL:
                        self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'] = int(updateValue)
                        if dev.states['batteryLevelRemote'] != float(updateValue):
                            updateKeyValueList.append({'key': 'batteryLevelRemote', 'value':  int(updateValue)})
                        if self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv'] != 0 and self.globals['trvc'][trvCtlrDevId]['batteryLevelRemote'] < self.globals['trvc'][trvCtlrDevId]['batteryLevelTrv']:
                            if dev.states['batteryLevel'] != float(updateValue):
                                updateKeyValueList.append({'key': 'batteryLevel', 'value':  int(updateValue)})

                    elif updateKey == UPDATE_REMOTE_TEMPERATURE:  
                        self.globals['trvc'][trvCtlrDevId]['temperatureRemote'] = float(updateValue)
                        self.globals['trvc'][trvCtlrDevId]['temperature'] = float(updateValue)
                        if dev.states['temperatureRemote'] != float(updateValue):
                            updateKeyValueList.append({'key': 'temperatureRemote', 'value':  float(updateValue)})
                            updateKeyValueList.append({'key': 'temperature', 'value':  float(updateValue)})
                            updateKeyValueList.append({'key': 'temperatureInput1', 'value': float(updateValue), 'uiValue': '{:.1f} °C'.format(float(updateValue))})
                            updateKeyValueList.append({'key': 'temperatureUi', 'value': 'R: {:.1f} °C, T: {:.1f} °C'.format(float(updateValue), float(self.globals['trvc'][trvCtlrDevId]['temperatureTrv']))})

                            # if spirit:

                    elif updateKey == UPDATE_REMOTE_HEAT_SETPOINT_FROM_DEVICE:
                        setpoint = float(updateValue)
                        if float(setpoint) < float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum']):
                            setpoint = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum'])
                        elif float(setpoint) > float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum']):
                            setpoint = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMaximum'])
                        self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote'] = float(setpoint)
                        self.globals['trvc'][trvCtlrDevId]['setpointHeat'] = float(setpoint)
                        if dev.states['setpointHeatRemote'] != float(setpoint):
                            updateKeyValueList.append({'key': 'setpointHeatRemote', 'value':  float(setpoint)})
                        if dev.states['setpointHeat'] != float(setpoint):
                            updateKeyValueList.append({'key': 'setpointHeat', 'value':  float(setpoint)})

                    elif updateKey == UPDATE_ZWAVE_EVENT_RECEIVED_TRV:
                        updateKeyValueList.append({'key': 'zwaveEventReceivedDateTimeTrv', 'value':  updateValue})

                    elif updateKey == UPDATE_ZWAVE_EVENT_RECEIVED_REMOTE:
                        updateKeyValueList.append({'key': 'zwaveEventReceivedDateTimeRemote', 'value':  updateValue})

                    elif updateKey == UPDATE_ZWAVE_EVENT_SENT_TRV:
                        updateKeyValueList.append({'key': 'zwaveEventSentDateTimeTrv', 'value':  updateValue})

                    elif updateKey == UPDATE_ZWAVE_EVENT_SENT_REMOTE:
                        updateKeyValueList.append({'key': 'zwaveEventSentDateTimeRemote', 'value':  updateValue})

                    elif updateKey == UPDATE_EVENT_RECEIVED_REMOTE:
                        updateKeyValueList.append({'key': 'eventReceivedDateTimeRemote', 'value':  updateValue})

                    elif updateKey == UPDATE_CONTROLLER_VALVE_PERCENTAGE:
                        self.globals['trvc'][trvCtlrDevId]['valvePercentageOpen'] = float(updateValue)
                        updateKeyValueList.append({'key': 'valvePercentageOpen', 'value':  updateValue})


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
                    updateDeviceStatesLog = updateDeviceStatesLog + u'\nXX  States to be updated in the TRV Controller device:'
                    for itemToUpdate in updateKeyValueList:
                        updateDeviceStatesLog = updateDeviceStatesLog + '\nXX    > {}'.format(itemToUpdate)
                    dev.updateStatesOnServer(updateKeyValueList)
                else:
                    updateDeviceStatesLog = updateDeviceStatesLog + u'\nXX  No States to be updated in the TRV Controller device:'


                updateDeviceStatesLog = updateDeviceStatesLog + u'\nXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX\n\n'
    
                self.trvHandlerLogger.debug(updateDeviceStatesLog)

                self.controlTrv(trvCtlrDevId)

                # self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_HIGH, 0, CMD_CONTROL_TRV, trvCtlrDevId, None])

                # self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0 ,CMD_CONTROL_HEATING_SOURCE, trvCtlrDevId, [self.globals['trvc'][trvCtlrDevId]['heatingId'], ]])


        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [updateDeviceStates]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

        except:
            self.trvHandlerLogger.error(u'Unexpected Exception detected in TRV Handler Thread [updateDeviceStates]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, sys.exc_info()[0]))


    def updateAllCsvFilesViaPostgreSQL(self, trvCtlrDevId, overrideDefaultRetentionHours, overrideCsvFilePrefix):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

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

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [updateAllCsvFilesViaPostgreSQL]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def _updateCsvFileViaPostgreSQL(self, trvCtlrDevId, overrideDefaultRetentionHours, overrideCsvFilePrefix, stateName):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')


            # Dynamically create CSV files from SQL Logger

            postgreSQLSupported = False
            try:
                user = self.globals['config']['postgresqlUser']
                password = self.globals['config']['postgresqlPassword']
                # self.trvHandlerLogger.error(u'PostgreSQL: User = \'{}\', Password = \'{}\''.format(user, password))   
                conn = None
                connString = "dbname=indigo_history user={} password={}".format(user, password)
                conn = psycopg2.connect(connString)
                if conn is not None:
                    postgreSQLSupported = True

            except StandardError, err:
                errString = '{}'.format(err)
                if errString.find('role') != -1 and errString.find('does not exist') != -1:
                    self.trvHandlerLogger.error(u'PostgreSQL user \'{}\' (specified in plugin config) is invalid'.format(user))
                else: 
                    self.trvHandlerLogger.error(u'PostgreSQL not supported or connection attempt invalid. Reason: {}'.format(err))

            if not postgreSQLSupported:
                return

            if overrideDefaultRetentionHours > 0:
                csvRetentionPeriodHours = overrideDefaultRetentionHours
            else:
                csvRetentionPeriodHours = self.globals['trvc'][trvCtlrDevId]['csvRetentionPeriodHours']
            dateTimeNow = datetime.datetime.now()
            checkTime = dateTimeNow - datetime.timedelta(hours=csvRetentionPeriodHours)
            checkTimeStr = checkTime.strftime("%Y-%m-%d %H:%M:%S.000000")

            cur = conn.cursor()
            selectString = "SELECT ts, {} FROM device_history_{} WHERE ( ts >= '{}' AND  {} IS NOT NULL )".format(stateName, trvCtlrDevId, checkTimeStr, stateName)  # YYYY-MM-DD HH:MM:SS
            cur.execute(selectString)
            rowCount = cur.rowcount
            rows = cur.fetchall()
            cur.close()

            # rowsLog = ''
            # for row in rows:
            #     timestamp = row[0].strftime("%Y-%m-%d %H:%M:%S.%f")
            #     temperature = row[1]
            #     rowsLog = rowsLog + '\n{},{}'.format(timestamp, temperature)


            # self.trvHandlerLogger.info(u'ROWS [{}] = \n{}\n'.format(rowCount, rowsLog))   

            cur = conn.cursor()
            selectString = "SELECT ts, {} FROM device_history_{} WHERE ( ts < '{}' AND {} IS NOT NULL) ORDER BY ts DESC LIMIT 1".format(stateName, trvCtlrDevId, checkTimeStr, stateName)  # YYYY-MM-DD HH:MM:SS

            cur.execute(selectString)
            rowCount = cur.rowcount
            droppedRow = cur.fetchone()
            cur.close()

            # self.trvHandlerLogger.info(u'LAST DROPPED ROW [{}] = \n{}\n'.format(rowCount, droppedRow))

            csvShortName = self.globals['trvc'][trvCtlrDevId]['csvShortName']

            if overrideCsvFilePrefix != '':
                csvFilePrefix = overrideCsvFilePrefix
            else:
                csvFilePrefix = self.globals['config']['csvPrefix']

            csvFileNamePathPrefix = '{}/{}'.format(self.globals['config']['csvPath'], csvFilePrefix)

            csvFilename = '{}_{}_{}.csv'.format(csvFileNamePathPrefix, csvShortName, stateName)

            headerName = '{} - {}'.format(indigo.devices[trvCtlrDevId].name, stateName)

            csvFileOut = open(csvFilename, 'w')

            self.trvHandlerLogger.debug(u'CSV FILE NAME = \'{}\', Time = \'{}\', State = \'{}\''.format(csvFilename, checkTimeStr, stateName))

            headerName = headerName.replace(',','_')  # Replace any commas with underscore to avoid CSV file problems
            csvFileOut.write('Timestamp,{}\n'.format(headerName))  # Write out header

            csvFileOut.write('{},{}\n'.format(checkTimeStr, droppedRow[1]))

            for row in rows:
                timestamp = row[0].strftime("%Y-%m-%d %H:%M:%S.%f")
                dataValue = row[1]
                csvFileOut.write('{},{}\n'.format(timestamp, dataValue))

            timestamp = dateTimeNow.strftime("%Y-%m-%d %H:%M:%S.999999")
            if len(rows) > 0:
                lastRow = rows[(len(rows) - 1)]
                dataValue = lastRow[1]
                csvFileOut.write('{},{}\n'.format(timestamp, dataValue))
            else:
                csvFileOut.write('{},{}\n'.format(timestamp, droppedRow[1]))

            csvFileOut.close()

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [_updateCsvFileViaPostgreSQL]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

        finally:
            if conn is not None:
                conn.close()


    def updateAllCsvFiles(self, trvCtlrDevId):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

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

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [updateAllCsvFiles]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def updateCsvFile(self, trvCtlrDevId, stateName, updateValue):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            if not self.globals['config']['csvStandardEnabled'] or self.globals['trvc'][trvCtlrDevId]['csvCreationMethod'] != 1:  # Standard CSV Output
                return

            dateTimeNow = datetime.datetime.now()
            dateTimeNowStr = dateTimeNow.strftime("%Y-%m-%d %H:%M:%S.%f")

            checkTime = dateTimeNow - datetime.timedelta(hours=self.globals['trvc'][trvCtlrDevId]['csvRetentionPeriodHours'])
            checkTimeStr = checkTime.strftime("%Y-%m-%d %H:%M:%S.%f")

            csvShortName = self.globals['trvc'][trvCtlrDevId]['csvShortName']
            csvFileNamePathPrefix = '{}/{}'.format(self.globals['config']['csvPath'], self.globals['config']['csvPrefix'])

            csvFilename = '{}_{}_{}.csv'.format(csvFileNamePathPrefix, csvShortName, stateName)

            headerName = '{} - {}'.format(indigo.devices[trvCtlrDevId].name, stateName)

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

            self.trvHandlerLogger.debug(u'CSV FILE NAME = \'{}\', Time = \'{}\', State = \'{}\', Value = \'{}\''.format(csvFilename, checkTimeStr, stateName, updateValue))


            headerName = headerName.replace(',','_')  # Replace any commas with underscore to avoid CSV file problems
            csvFileOut.write('Timestamp,{}\n'.format(headerName))  # Write out header

            droppedRowsFlag = False
            droppedRow = ''
            writtenRowCount = 0
            firstRowWrittenFlag = False 
            for row in dataIn:
                if row[0:26] < checkTimeStr: # e.g. 2017-04-09 17:26:13.956000
                    droppedRowsFlag = True
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
                        csvFileOut.write('{}\n'.format(firstRow))  # Output modified CSV data line 
                csvFileOut.write('{}\n'.format(row))  # Output CSV data line as not older than retention limit
            csvFileOut.write('{},{}\n'.format(dateTimeNowStr, updateValue))
            csvFileOut.close()

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [updateCsv]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def controlTrv(self, trvCtlrDevId):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            # Control the thermostat that is controlled by this TRV Controller (trvCtlrDevId)

            if not self.globals['trvc'][trvCtlrDevId]['deviceStarted']:
                self.trvHandlerLogger.debug(u'controlTrv: \'{}\' startup not yet completed'.format(indigo.devices[trvCtlrDevId].name)) 
                return

            trvDevId = self.globals['trvc'][trvCtlrDevId]['trvDevId']
            trvDev = indigo.devices[trvDevId]
            remoteDevId = self.globals['trvc'][trvCtlrDevId]['remoteDevId']

            self.trvHandlerLogger.debug(u'controlTrv: \'{}\' is set to Controller Mode \'{}\''.format(indigo.devices[trvCtlrDevId].name, CONTROLLER_MODE_TRANSLATION[self.globals['trvc'][trvCtlrDevId]['controllerMode']])) 
            self.trvHandlerLogger.debug(u'controlTrv: \'{}\' internal states [1] are: setpointHeat = {}, setPointTrv =  {}'.format(indigo.devices[trvCtlrDevId].name, self.globals['trvc'][trvCtlrDevId]['setpointHeat'], self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])) 

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

            self.trvHandlerLogger.debug(u'controlTrv: \'{}\' internal states [2] are: setpointHeat = {}, setPointTrv =  {}'.format(indigo.devices[trvCtlrDevId].name, self.globals['trvc'][trvCtlrDevId]['setpointHeat'], self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])) 


            # Set the Remote Thermostat setpoint if not invoked by remote and it exists and setpoint adjustment is enabled

            if self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_AUTO or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_UI or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_TRV_HARDWARE or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_TRV_UI:
                if remoteDevId != 0 and self.globals['trvc'][trvCtlrDevId]['remoteSetpointHeatControl']:
                    if float(indigo.devices[remoteDevId].heatSetpoint) != float(self.globals['trvc'][trvCtlrDevId]['setpointHeat']):
                        self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeat'])
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointFlag'] = True
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointSequence'] += 1
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingRemoteSetpointValue'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])
                        indigo.thermostat.setHeatSetpoint(remoteDevId, value=float(self.globals['trvc'][trvCtlrDevId]['setpointHeat']))  # Set Remote Heat Setpoint to Target Temperature
                        self.trvHandlerLogger.debug(u'controlTrv: Adjusting Remote Setpoint Heat from {} to Target Temperature of {}'.format(float(indigo.devices[remoteDevId].heatSetpoint), float(self.globals['trvc'][trvCtlrDevId]['setpointHeat'])))
                        indigo.devices[trvCtlrDevId].updateStateOnServer(key='setpointHeatRemote', value=float(self.globals['trvc'][trvCtlrDevId]['setpointHeatRemote']))                    

            hvacFullPower = False
            if trvDev.model == 'Thermostat (Spirit)' and 'zwaveHvacOperationModeID' in trvDev.states and trvDev.states['zwaveHvacOperationModeID'] == HVAC_FULL_POWER:
                hvacFullPower = True

            self.trvHandlerLogger.debug(u'controlTrv: \'{}\' internal states [3] are: HVAC_FULL_POWER = {}'.format(indigo.devices[trvCtlrDevId].name, hvacFullPower)) 


            if (float(self.globals['trvc'][trvCtlrDevId]['setpointHeat']) <= float(self.globals['trvc'][trvCtlrDevId]['temperature'])) and not hvacFullPower:

                self.controlTrvHeatingOff(trvCtlrDevId)  # TRV no longer calling for heat
                self.controlHeatingSource(trvCtlrDevId, self.globals['trvc'][trvCtlrDevId]['heatingId'], self.globals['trvc'][trvCtlrDevId]['heatingVarId'])

                if self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_AUTO or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_UI or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_REMOTE_HARDWARE or self.globals['trvc'][trvCtlrDevId]['controllerMode'] == CONTROLLER_MODE_REMOTE_UI: 

                    if float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']) != float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum']):
                        self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatMinimum'])
                    if indigo.devices[trvDevId].heatSetpoint != float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']):
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointFlag'] = True
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointSequence'] += 1
                        self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointValue'] = float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv'])
                        indigo.thermostat.setHeatSetpoint(trvDevId, value=float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']))
                        self.trvHandlerLogger.debug(u'controlTrv: Turning OFF and adjusting TRV Setpoint Heat to \'{}\'. Z-Wave Pending = {}, Setpoint = \'{}\', Sequence = \'{}\'.'.format(float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']), self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointFlag'], self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointValue'], self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointSequence']))
                        indigo.devices[trvCtlrDevId].updateStateOnServer(key='setpointHeatTrv', value=float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']))                    

                        if self.globals['trvc'][trvCtlrDevId]['valveDevId'] != 0:  # e.g. EUROTronic Spirit Thermostat
                            if self.globals['trvc'][trvCtlrDevId]['valveAssistance']:
                                self.trvHandlerLogger.debug(u'controlTrv: >>>>>> \'{}\' SUPPORTS VALVE CONTROL - CLOSING VALVE <<<<<<<<<'.format(indigo.devices[trvDevId].name))

                                zwaveRawCommandSequence = list()
                                zwaveRawCommandSequence.append((1, 0, [], 'Timer Initialisation'))
                                zwaveRawCommandSequence.append((1, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x1F], 'Thermostat Mode Control - Valve Control'))
                                zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x01, 0x00], 'Switch Multilevel - Valve = 0%'))
                                zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x01, 0x00], 'Switch Multilevel - Valve = 0%'))
                                zwaveRawCommandSequence.append((2, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x02], 'Switch Multilevel - Status Update'))
                                zwaveRawCommandSequence.append((2, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x02], 'Switch Multilevel - Status Update'))
                                zwaveRawCommandSequence.append((1, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x01], 'Thermostat Mode Control - Heat'))
                                self.controlTrvSpiritValveCommandsQueued(trvCtlrDevId, zwaveRawCommandSequence)

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
                        self.trvHandlerLogger.debug(u'controlTrv: Turning ON and adjusting TRV Setpoint Heat to \'{}\'. Z-Wave Pending = {}, Setpoint = \'{}\', Sequence = \'{}\'.'.format(float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']), self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointFlag'], self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointValue'], self.globals['trvc'][trvCtlrDevId]['zwavePendingTrvSetpointSequence']))
                        indigo.devices[trvCtlrDevId].updateStateOnServer(key='setpointHeatTrv', value=float(self.globals['trvc'][trvCtlrDevId]['setpointHeatTrv']))                    

                        if self.globals['trvc'][trvCtlrDevId]['valveDevId'] != 0:  # EUROTronic Spirit Thermostat special logic
                            if self.globals['trvc'][trvCtlrDevId]['valveAssistance']:
                                self.trvHandlerLogger.debug(u'controlTrv: >>>>>> \'{}\' SUPPORTS VALVE CONTROL - OPENING VALVE <<<<<<<<<'.format(indigo.devices[trvDevId].name))

                                zwaveRawCommandSequence = list()
                                zwaveRawCommandSequence.append((1, 0, [], 'Timer Initialisation'))
                                zwaveRawCommandSequence.append((1, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x1F], 'Thermostat Mode Control - Valve Control'))
                                zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x01, 0x63], 'Switch Multilevel - Valve = 100%'))
                                zwaveRawCommandSequence.append((3, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x01, 0x63], 'Switch Multilevel - Valve = 100%'))
                                zwaveRawCommandSequence.append((2, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x02], 'Switch Multilevel - Status Update'))
                                zwaveRawCommandSequence.append((2, self.globals['trvc'][trvCtlrDevId]['valveDevId'], [0x26, 0x02], 'Switch Multilevel - Status Update'))
                                zwaveRawCommandSequence.append((0, self.globals['trvc'][trvCtlrDevId]['trvDevId'], [0x40, 0x01, 0x01], 'Thermostat Mode Control - Heat'))
                                self.controlTrvSpiritValveCommandsQueued(trvCtlrDevId, zwaveRawCommandSequence)

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [controlTRV]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

        except:
            self.trvHandlerLogger.error(u'Unexpected Exception detected in TRV Handler Thread [controlTRV]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, sys.exc_info()[0]))


    def controlTrvSpiritValveCommandsQueued(self, trvCtlrDevId, zwaveRawCommandSequence):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            spiritValveId = self.globals['trvc'][trvCtlrDevId]['valveDevId']
            spiritValveDev = indigo.devices[spiritValveId]
            self.trvHandlerLogger.debug(u'controlTrvSpiritQueued')

            if trvCtlrDevId in self.globals['timers']['SpiritValveCommands']:
                self.globals['timers']['SpiritValveCommands'][trvCtlrDevId].cancel()
                self.trvHandlerLogger.debug(u'controlTrvSpiritValveCommandsQueued timer cancelled for device \'{}\' with now cancelled Command Sequence:\n{}'.format(spiritValveDev.name, zwaveRawCommandSequence))


            self.controlTrvSpiritTriggered(trvCtlrDevId, zwaveRawCommandSequence)

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [controlTrvSpiritValveCommandsQueued]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

    def controlTrvSpiritTriggered(self, trvCtlrDevId, zwaveRawCommandSequence):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            spiritValveId = self.globals['trvc'][trvCtlrDevId]['valveDevId']
            spiritValveDev = indigo.devices[spiritValveId]

            self.trvHandlerLogger.debug(u'controlTrvSpiritTriggered for device \'{}\' with Command Sequence [{}]:\n\n{}\n'.format(spiritValveDev.name, len(zwaveRawCommandSequence), zwaveRawCommandSequence))

            seconds, targetDeviceId, zwaveRawCommandString, zwaveRawCommandDescription  = zwaveRawCommandSequence.pop(0)  # FIFO List
            if len(zwaveRawCommandString) > 0:       
                indigo.zwave.sendRaw(device = indigo.devices[targetDeviceId], cmdBytes = zwaveRawCommandString, sendMode = 1)
                self.trvHandlerLogger.debug(u'>>>>>> ZWave Raw Command for device \'{}\' = {}'.format(indigo.devices[targetDeviceId].name, zwaveRawCommandDescription))

            if len(zwaveRawCommandSequence) > 0:
                delaySeconds = zwaveRawCommandSequence[0][0]
                self.globals['timers']['SpiritValveCommands'][trvCtlrDevId] = threading.Timer(float(delaySeconds), self.controlTrvSpiritTriggered, [trvCtlrDevId, zwaveRawCommandSequence])
                self.globals['timers']['SpiritValveCommands'][trvCtlrDevId].setDaemon(True)
                self.globals['timers']['SpiritValveCommands'][trvCtlrDevId].start()

        except StandardError, err:
            self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [controlTrvSpiritTriggered]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   



    def controlTrvHeatingOff(self, trvCtlrDevId):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            try:
                self.globals['lock'].acquire()
                if self.globals['trvc'][trvCtlrDevId]['heatingId'] > 0:
                    self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['thermostatsCallingForHeat'].discard(trvCtlrDevId)  # Remove TRV Controller from the SET thermostatsCallingForHeat
                if self.globals['trvc'][trvCtlrDevId]['heatingVarId'] > 0:
                    self.globals['heaterVariables'][self.globals['trvc'][trvCtlrDevId]['heatingVarId']]['thermostatsCallingForHeat'].discard(trvCtlrDevId)  # Remove TRV Controller from the SET thermostatsCallingForHeat
            except StandardError, err:
                self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [controlTrvHeatingOff]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   
            except:
                self.trvHandlerLogger.error(u'Unexpected Exception detected in TRV Handler Thread [controlTrvHeatingOff]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, sys.exc_info()[0]))
            finally:
                self.globals['lock'].release()

                indigo.devices[trvCtlrDevId].updateStateOnServer(key='hvacHeaterIsOn', value=False)
                indigo.devices[trvCtlrDevId].updateStateImageOnServer(indigo.kStateImageSel.HvacHeatMode)  # HvacOff - HvacHeatMode - HvacHeating - HvacAutoMode

        except:
            self.trvHandlerLogger.error(u'Unexpected Exception detected in TRV Handler Thread [controlTrvHeatingOff]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, sys.exc_info()[0]))

    def controlTrvHeatingOn(self, trvCtlrDevId):

        try:
            self.methodTracer.threaddebug(u'TrvHandler Method')

            try:
                self.globals['lock'].acquire()
                if self.globals['trvc'][trvCtlrDevId]['heatingId'] > 0:
                    self.globals['heaterDevices'][self.globals['trvc'][trvCtlrDevId]['heatingId']]['thermostatsCallingForHeat'].add(trvCtlrDevId)  # Add TRV Controller to the SET thermostatsCallingForHeat
                if self.globals['trvc'][trvCtlrDevId]['heatingVarId'] > 0:
                    self.globals['heaterVariables'][self.globals['trvc'][trvCtlrDevId]['heatingVarId']]['thermostatsCallingForHeat'].add(trvCtlrDevId)  # Add TRV Controller to the SET thermostatsCallingForHeat
                self.globals['trvc'][trvCtlrDevId]['hvacOperationModeTrv'] = HVAC_HEAT
            except StandardError, err:
                self.trvHandlerLogger.error(u'StandardError detected in TRV Handler Thread [controlTrvHeatingOn]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   
            except:
                self.trvHandlerLogger.error(u'Unexpected Exception detected in TRV Handler Thread [controlTrvHeatingOn]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, sys.exc_info()[0]))
            finally:
                self.globals['lock'].release()

                indigo.devices[trvCtlrDevId].updateStateOnServer(key='hvacHeaterIsOn', value=True)
                indigo.devices[trvCtlrDevId].updateStateImageOnServer(indigo.kStateImageSel.HvacHeatMode)  # HvacOff - HvacHeatMode - HvacHeating - HvacAutoMode

        except:
            self.trvHandlerLogger.error(u'Unexpected Exception detected in TRV Handler Thread [controlTrvHeatingOff]. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, sys.exc_info()[0]))

