#! /usr/bin/env python
# -*- coding: utf-8 -*-
#
# Delay Handler © Autolog 2020
#

try:
    # noinspection PyUnresolvedReferences
    import indigo
except ImportError:
    pass

import logging

import queue
import sys
import threading
import time
import traceback

from constants import *


# noinspection PyUnresolvedReferences,PyPep8Naming
class ThreadDelayHandler(threading.Thread):

    # This class handles Delay Queue processing

    def __init__(self, pluginGlobals, event):

        threading.Thread.__init__(self)

        self.globals = pluginGlobals

        self.delayHandlerLogger = logging.getLogger("Plugin.TRV_DH")
        self.delayHandlerLogger.debug("Debugging Delay Handler Thread")

        self.threadStop = event

    def exception_handler(self, exception_error_message, log_failing_statement):
        filename, line_number, method, statement = traceback.extract_tb(sys.exc_info()[2])[-1]
        module = filename.split('/')
        log_message = f"'{exception_error_message}' in module '{module[-1]}', method '{method}'"
        if log_failing_statement:
            log_message = log_message + f"\n   Failing statement [line {line_number}]: '{statement}'"
        else:
            log_message = log_message + f" at line {line_number}"
        self.delayHandlerLogger.error(log_message)

    def run(self):

        try:
            self.delayHandlerLogger.debug('Delay Handler Thread initialised')

            while not self.threadStop.is_set():
                try:
                    delayQueuedEntry = self.globals['queues']['delayHandler'].get(True, 5)

                    # delayQueuedEntry format:
                    #   - Device
                    #   - Polling Sequence

                    # self.delayHandlerLogger.debug(f'DEQUEUED MESSAGE = {delayQueuedEntry}')
                    # trvCommand, trvCommandDevId, pollingSequence = delayQueuedEntry
                    trvCommand, trvCommandDevId = delayQueuedEntry

                    if trvCommand == CMD_STOP_THREAD:
                        break  # Exit While loop and quit thread

                    # Check if monitoring / debug options have changed and if so set accordingly
                    if self.globals['debug']['previousDelayHandler'] != self.globals['debug']['delayHandler']:
                        self.globals['debug']['previousDelayHandler'] = self.globals['debug']['delayHandler']
                        self.delayHandlerLogger.setLevel(self.globals['debug']['delayHandler'])

                    if trvCommand != CMD_ACTION_POLL:
                        continue

                    self.globals['queues']['trvHandler'].put([QUEUE_PRIORITY_STATUS_MEDIUM, 0, CMD_ACTION_POLL, trvCommandDevId, []])

                    delay_time = self.globals['config']['delayQueueSeconds']
                    self.delayHandlerLogger.debug(
                        f'DELAY QUEUE ENTRY RETRIEVED FOR DEVICE: {indigo.devices[trvCommandDevId].name}, Command is \'{CMD_TRANSLATION[CMD_ACTION_POLL]}\'.\nDELAYING FOR {delay_time} SECONDS. Remaining queue size is {self.globals["queues"]["delayHandler"].qsize()}')

                    time.sleep(delay_time)
                    self.delayHandlerLogger.debug(f'DELAY COMPLETED AFTER {delay_time} SECONDS.\nRemaining queue size is {self.globals["queues"]["delayHandler"].qsize()}')

                except queue.Empty:
                    pass
                except Exception as exception_error:
                    self.exception_handler(exception_error, True)  # Log error and display failing statement

        except Exception as exception_error:
            self.exception_handler(exception_error, True)  # Log error and display failing statement

        self.delayHandlerLogger.debug('Delay Handler Thread ended.')
