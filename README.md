# Grafana Growatt Dashboard

## Config Change after cloning the repository

- username and password in `config.json` is server-api.growatt.com/login username/pass
- plant_id from [`get_plants`](https://github.com/neelabalan/growatt_dashboard/blob/b3fc477412518facce65164e8bd38b5d9734289d/data_collector.py#LL58C9-L58C19)
- start_date - Date from which data has to be collected

## Setup

- Once the `config.json` changes are done, run `docker-compose up` to start the `data_collector.py` and `grafana` service.
- Navigate to `localhost:3000` **username: admin, password: admin** (check `grafana.ini` in grafana_config folder)
- In the dashboard section select **Browse** and select **GrowattDashboard** to view.


# Screenshot


![screenshot](https://github.com/neelabalan/growatt_dashboard/blob/main/img/dashboard_screenshot.png)
