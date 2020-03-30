#! /usr/bin/env python
# -*- coding: utf-8 -*-
#
# Delay Handler © Autolog 2020
#

try:
    # noinspection PyUnresolvedReferences
    import indigo
except ImportError, e:
    pass

import logging

import Queue
import sys
import threading
import time

from constants import *


# noinspection PyUnresolvedReferences,PyPep8Naming
class ThreadDelayHandler(threading.Thread):

    # This class handles Delay Queue processing

    def __init__(self, pluginGlobals, event):

        threading.Thread.__init__(self)

        self.globals = pluginGlobals

        self.delayHandlerLogger = logging.getLogger("Plugin.delayHandler")
        self.delayHandlerLogger.setLevel(self.globals['debug']['delayHandler'])

        self.methodTracer = logging.getLogger("Plugin.method")
        self.methodTracer.setLevel(self.globals['debug']['methodTrace'])

        self.delayHandlerLogger.debug(u"Debugging Delay Handler Thread")

        self.threadStop = event

    def run(self):

        try:
            self.methodTracer.threaddebug(u'DelayHandler Method')

            # Initialise routine on thread start
            pass

            self.delayHandlerLogger.debug(u'Delay Handler Thread initialised')

            while not self.threadStop.is_set():
                try:
                    delayQueuedEntry = self.globals['queues']['delayHandler'].get(True, 5)

                    # delayQueuedEntry format:
                    #   - Priority
                    #   - Command
                    #   - Device
                    #   - Data

                    # self.delayHandlerLogger.debug(u'DEQUEUED MESSAGE = {}'.format(delayQueuedEntry))
                    trvQueuePriority, trvQueueSequence, trvCommand, trvCommandDevId, trvCommandPackage = delayQueuedEntry

                    if trvCommand == CMD_STOP_THREAD:
                        self.globals['queues']['trvHandler'].put(delayQueuedEntry)  # Pass CMD_STOP_THREAD on to TRV Handler befor stoppin Delay handler
                        break  # Exit While loop and quit thread


                    # Check if monitoring / debug options have changed and if so set accordingly
                    if self.globals['debug']['previousDelayHandler'] != self.globals['debug']['delayHandler']:
                        self.globals['debug']['previousDelayHandler'] = self.globals['debug']['delayHandler']
                        self.delayHandlerLogger.setLevel(self.globals['debug']['delayHandler'])
                    if self.globals['debug']['previousMethodTrace'] != self.globals['debug']['methodTrace']:
                        self.globals['debug']['previousMethodTrace'] = self.globals['debug']['methodTrace']
                        self.methodTracer.setLevel(self.globals['debug']['methodTrace'])

                    if trvCommand == CMD_UPDATE_VALVE_STATES and self.globals['config']['delayQueueSecondsForValveCommand'] > 0:
                        delay_time = self.globals['config']['delayQueueSecondsForValveCommand']
                    else:
                        delay_time = self.globals['config']['delayQueueSeconds']
                    self.delayHandlerLogger.debug(u'DELAY QUEUE ENTRY RETRIEVED AND DELAYED {} SECONDS FOR DEVICE: {}, Command is \'{}\'. Remaining queue size is {}'.format(delay_time, indigo.devices[trvCommandDevId].name, CMD_TRANSLATION[trvCommand], self.globals['queues']['delayHandler'].qsize()))
                    time.sleep(delay_time)
                    self.delayHandlerLogger.debug(u'DELAY QUEUE ENTRY RELEASED AFTER {} SECONDS FOR DEVICE: {}, Command is \'{}\'. Remaining queue size is {}'.format(delay_time, indigo.devices[trvCommandDevId].name, CMD_TRANSLATION[trvCommand], self.globals['queues']['delayHandler'].qsize()))

                    self.globals['queues']['trvHandler'].put(delayQueuedEntry)

                except Queue.Empty:
                    pass
                except StandardError, err:
                    self.delayHandlerLogger.error(u'StandardError detected in Delay Handler Thread. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))  
                except:
                    self.delayHandlerLogger.error(u'Unexpected Exception detected in Delay Handler Thread. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, sys.exc_info()[0]))

        except StandardError, err:
            self.delayHandlerLogger.error(u'StandardError detected in Delay Handler Thread. Line \'{}\' has error=\'{}\''.format(sys.exc_traceback.tb_lineno, err))   

        self.delayHandlerLogger.debug(u'Delay Handler Thread ended.')