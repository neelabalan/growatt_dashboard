import argparse
import calendar
import datetime
import json
import logging
import pathlib
import sqlite3
import sys
from typing import Dict
from typing import List
from typing import Tuple

import requests
from dateutil import relativedelta
from apscheduler.schedulers.background import BlockingScheduler

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

DATABASE_NAME = "data/solar_data.sqlite"
scheduler = BlockingScheduler()
SCHEDULER_INTERVAL = 12


# Based on https://github.com/Antonji-py/solar-providers-manager
class GrowattApi:
    def __init__(self, username, password):
        session = requests.Session()
        session.headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
        }

        self.login(session, username, password)
        self.session = session

    def __fetch_url(self, action: str = "login") -> str:
        return {
            "login": "http://server-api.growatt.com/login",
            "list_plants": "http://server-api.growatt.com/selectPlant/getPlantList",
            "plant_devices": "http://server-api.growatt.com/panel/getDevicesByPlantList",
            "get_daily_logs_tlx": "http://server-api.growatt.com/device/getTLXHistory",
            "get_daily_logs_inv": "http://server-api.growatt.com/device/getInverterHistor",
            "get_monthly_energy": "http://server-api.growatt.com/energy/compare/getDevicesMonthChart",
            "get_daily_energy": "http://server-api.growatt.com/energy/compare/getDevicesDayChart",
        }.get(action)

    def login(self, session: requests.Session, username: str, password: str) -> None:
        data = {"account": username, "password": password, "validateCode": ""}

        response = session.post(self.__fetch_url("login"), data=data)
        if response.json()["result"] != 1:
            logger.error("[Growatt] Error while logging in")
            raise Exception("Error logging in")
        logger.info("[Growatt] Successfully logged in")

    def get_plants(self) -> List[Dict]:
        current_page, pages, plants_list = 0, -1, []

        while current_page != pages:
            current_page += 1
            response = self.session.post(
                self.__fetch_url("list_plants"),
                data={
                    "currPage": current_page,
                    "plantType": "-1",
                    "orderType": 0,
                    "plantName": "",
                },
            )
            response_json = response.json()
            pages = response_json["pages"]
            plants_list.extend(
                [
                    {"id": plant["id"], "plant_name": plant["plantName"]}
                    for plant in response_json["datas"]
                ]
            )
        return plants_list

    def get_plant_devices(self, plant_id: str) -> List[Dict]:
        current_page, pages, devices = 0, -1, []

        while current_page != pages:
            current_page += 1
            response = self.session.post(
                self.__fetch_url("plant_devices"),
                data={"currPage": current_page, "plantId": plant_id},
            )
            response_json = response.json()
            if response_json["result"] != 1:
                return response_json
            pages = response_json["obj"]["pages"]
            devices.extend([device for device in response_json["obj"]["datas"]])
        return devices

    def get_daily_logs(
        self, device_id: str, date: str, device_type="tlx"
    ) -> List[Dict]:
        """device type can be tlx or inv"""
        url = self.__fetch_url(f"get_daily_logs_{device_type}")
        device_key = "invSn" if device_type == "inv" else "tlxSn"
        logs, start_index, have_next = [], 0, True

        while have_next:
            response = self.session.post(
                url,
                data={
                    device_key: device_id,
                    "startDate": date,
                    "endDate": date,
                    "start": start_index,
                },
            )
            response_json = response.json()

            for log in response_json["obj"]["datas"]:
                logs.append(log)

            start_index = response_json["obj"]["start"]
            have_next = response_json["obj"]["haveNext"]

        logs.reverse()
        return logs

    def get_monthly_energy_data(self, plant_id: str, date: str) -> List[Dict]:
        url = self.__fetch_url("get_monthly_energy")
        response = self.session.post(
            url,
            data={
                "plantId": plant_id,
                "jsonData": json.dumps(
                    [{"type": "plant", "sn": plant_id, "params": "energy,autoEnergy"}]
                ),
                "date": date,
            },
        )
        return response.json()

    def get_daily_energy_data(self, plant_id: str, date: str) -> List[Dict]:
        url = self.__fetch_url("get_daily_energy")
        response = self.session.post(
            url,
            data={
                "plantId": plant_id,
                "jsonData": json.dumps(
                    [{"type": "plant", "sn": plant_id, "params": "energy,autoEnergy"}]
                ),
                "date": date,
            },
        )
        return response.json()


class Job:
    PAC = "pac"
    KWH = "kwh"

    def __init__(self, conf_path):
        self.conf = self.__load_conf(conf_path)
        self.__validate_conf(self.conf)
        self.api = GrowattApi(self.conf.get("username"), self.conf.get("password"))

    def __load_conf(self, conf_path) -> Dict:
        conf = {}
        with open(conf_path, "r") as file:
            conf = json.load(file)
        return conf

    def __validate_conf(self, conf: Dict):
        if not conf.get("username") and not conf.get("password"):
            Exception("username/password missing in conf")
        if not conf.get("plant_id"):
            Exception("plant_id missing in conf")

    def diff_month(self, current, previous):
        return (current.year - previous.year) * 12 + current.month - previous.month

    def backfill_data(self):
        self.__create_table()  # first create table before starting operations
        start_date = datetime.datetime.strptime(
            self.conf.get("start_date"), "%Y-%m-%d"
        ).date()
        days = (datetime.datetime.now().date() - start_date).days + 1
        months = self.diff_month(datetime.datetime.now().date(), start_date) + 1

        logger.info(f"Backfilling Pac for {days} days")
        for count in range(days):
            self._insert(
                self.get_time_series_data_pac(
                    start_date + datetime.timedelta(days=count)
                ),
                table_name=self.PAC,
            )

        logger.info(f"Backfilling Kwh for {months} months")
        for count in range(months):
            self._insert(
                self.get_time_series_data_kwh(
                    start_date + relativedelta.relativedelta(months=count)
                ),
                table_name=self.KWH,
            )

    def get_time_series_data_pac(self, date: datetime.date) -> List[Tuple]:
        data = self.api.get_daily_energy_data(
            self.conf.get("plant_id"),
            f"{date.year}-{date.month}-{date.day}",
        )
        date = datetime.datetime.combine(date, datetime.datetime.min.time())
        return list(
            zip(
                # converting to string type to enter DB
                # 5 minutes, 288 times ~24 hours
                [
                    (date + datetime.timedelta(minutes=5 * count)).timestamp()
                    for count in range(288)
                ],
                list(
                    map(
                        lambda x: 0 if not x else x,
                        data.get("obj")[0]["datas"]["pac"],
                    )
                ),
            )
        )

    def get_time_series_data_kwh(self, date: datetime.date) -> List[Tuple]:
        data = self.api.get_monthly_energy_data(
            self.conf.get("plant_id"),
            f"{date.year}-{date.month}",
        )
        date = datetime.datetime.combine(date, datetime.datetime.min.time()).replace(
            day=1
        )

        return list(
            zip(
                # daily data
                [
                    (date + datetime.timedelta(days=count)).timestamp()
                    for count in range(calendar.monthrange(date.year, date.month)[1])
                ],
                list(
                    map(
                        lambda x: 0 if not x else x,
                        data.get("obj")[0]["datas"]["energy"],
                    )
                ),
            )
        )

    def run(self, backfill=False):
        if backfill:
            self.backfill_data()
            logger.info("Backfilling completed")
            sys.exit(0)
        today = datetime.datetime.now()
        self._insert(self.get_time_series_data_pac(today.date()), table_name=self.PAC)
        # last date
        self._insert(
            self.get_time_series_data_kwh(today.date())[-1], table_name=self.KWH
        )

    def __create_table(self):
        connection = sqlite3.connect(DATABASE_NAME)
        cursor = connection.cursor()
        cursor.execute(f"CREATE TABLE {self.PAC}(timestamp PRIMARY KEY, watt)")
        cursor.execute(f"CREATE TABLE {self.KWH}(timestamp PRIMARY KEY, kilowatthour)")

    def _insert(self, time_series_data: List[Tuple], table_name: str) -> None:
        connection = sqlite3.connect(DATABASE_NAME)
        cursor = connection.cursor()
        cursor.executemany(
            f"INSERT OR REPLACE INTO {table_name} VALUES(?, ?)", time_series_data
        )
        connection.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conf", action="append", default="config.json")
    args = parser.parse_args()
    job = Job(args.conf).run(backfill=not pathlib.Path(DATABASE_NAME).exists())
    # job.run(not pathlib.Path(DATABASE_NAME).exists())

    scheduler.add_job(
        job.run,
        trigger="interval",
        hours=SCHEDULER_INTERVAL,
        coalesce=True,
        args=(not pathlib.Path(DATABASE_NAME).exists(),),
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
