#! /usr/bin/env python
# -*- coding: utf-8 -*-
#

import postgresql

user = "postgres"
password = "tactile-forehead-OSSEOUS-buzzer-2311"
database_open_string = f"pq://{user}:{password}@127.0.0.1:5432/indigo_history"

database = postgresql.open(database_open_string)




stateName = "onOffState"
trvCtlrDevId = "824207921"
checkTimeStr = "2022-04-01 00:00:00"
selectString = f"SELECT ts, {stateName} FROM device_history_{trvCtlrDevId} WHERE ( ts >= '{checkTimeStr}' AND  {stateName} IS NOT NULL) ORDER BY ts"  # NOQA - YYYY-MM-DD HH:MM:SS
print(f"Select String: {selectString}")

ps = database.prepare(selectString)

rows = ps()
count = 0
for row in rows:
    timestamp = row[0].strftime("%Y-%m-%d %H:%M:%S.%f")
    dataValue = row[1]
    count += 1
    print(f"ID={count}, Timestamp: {timestamp}, Data Value: {dataValue}")

selectString2 = (f"SELECT ts, {stateName} FROM device_history_{trvCtlrDevId} WHERE ( ts < '{checkTimeStr}' AND {stateName} IS NOT NULL) ORDER BY ts DESC LIMIT 1")    # noqa [suppress no data sources help message] - YYYY-MM-DD HH:MM:SS
ps2 = database.prepare(selectString)
droppedRows = ps2()
droppedRow = droppedRows[0]

timestamp = droppedRow[0].strftime("%Y-%m-%d %H:%M:%S.%f")
dataValue = droppedRow[1]
print(f"Dropped Row = Timestamp: {timestamp}, Data Value: {dataValue}")
