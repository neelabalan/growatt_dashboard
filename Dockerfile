FROM python:3.8-bullseye

WORKDIR /opt/growatt_dashboard

COPY requirements.txt /opt/growatt_dashboard

RUN pip3 install -r /opt/growatt_dashboard/requirements.txt

COPY . /opt/growatt_dashboard/

ENTRYPOINT [ "python3", "data_collector.py" ]
