import logging
import sqlite3
from collections import OrderedDict

import dash_bootstrap_components as dbc
from dash import html

from psa_car_controller.psacc.application.psa_client import PSAClient
from psa_car_controller.psacc.repository.db import Database
from psa_car_controller.web.app import dash_app
from psa_car_controller.web.tools.Button import Button
from psa_car_controller.web.tools.Switch import Switch
from psa_car_controller.web.tools.utils import card_value_div, create_card

logger = logging.getLogger(__name__)

REFRESH_SWITCH = "refresh-switch"
ABRP_SWITCH = 'abrp-switch'
CHARGE_SWITCH = "charge-switch"
PRECONDITIONING_SWITCH = "preconditioning-switch"


def convert_value_to_str(value):
    try:
        return str(int(value))
    except (TypeError, ValueError, OverflowError):
        return "-"


def _get_energy(status, energy_type):
    if status is None:
        return None
    try:
        return status.get_energy(energy_type)
    except (AttributeError, TypeError):
        return None


def _get_vehicle_status(myp, car):
    if car.status is not None:
        return car.status
    try:
        return myp.get_vehicle_info(car.vin, cache=True)
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to refresh vehicle status for control tab: %s", car.vin)
        return None


def _get_status_cards(car, status, electric_energy):
    cards = OrderedDict()
    if car.has_battery():
        cards["Battery SOC"] = {
            "text": [card_value_div(f"battery-value-{car.vin}", "%",
                                    value=convert_value_to_str(getattr(electric_energy, "level", None)))],
            "src": dash_app.get_asset_url("images/battery-charge.svg")
        }

    odometer = getattr(status, "timed_odometer", None)
    cards["Mileage"] = {
        "text": [card_value_div(f"mileage-value-{car.vin}", "km",
                                value=convert_value_to_str(getattr(odometer, "mileage", None)))],
        "src": dash_app.get_asset_url("images/mileage.svg")
    }

    if car.has_battery():
        try:
            soh = Database.get_last_soh_by_vin(car.vin)
        except sqlite3.Error:
            logger.exception("Failed to read battery SOH for %s", car.vin)
            soh = None
        if soh is not None:
            cards["Battery SOH"] = {
                "text": [card_value_div(f"battery-soh-value-{car.vin}", "%",
                                        value=convert_value_to_str(soh))],
                "src": dash_app.get_asset_url("images/battery-soh.svg")
            }
            cards.move_to_end("Mileage")
    return cards


def _get_refresh_label(status):
    updated_at = None
    for energy_type in ("Electric", "Fuel"):
        energy = _get_energy(status, energy_type)
        updated_at = getattr(energy, "updated_at", None)
        if updated_at is not None:
            break
    try:
        text = updated_at.astimezone().strftime("%X %x")
    except (AttributeError, TypeError, ValueError):
        text = "Refresh"
    return html.Div([
        html.Img(src=dash_app.get_asset_url("images/sync.svg"), width="50px"),
        text
    ])


def _append_control(buttons, control_name, vin, factory):
    try:
        buttons.append(factory().get_html())
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to create %s control for %s", control_name, vin)


def _get_control_buttons(config, myp, car, status, electric_energy):
    buttons = []
    if config.remote_control:
        _append_control(buttons, "refresh", car.vin,
                        lambda: Button(REFRESH_SWITCH, car.vin, _get_refresh_label(status),
                                       myp.remote_client.wakeup))

        charging = getattr(electric_energy, "charging", None)
        charging_status = getattr(charging, "status", None)
        if car.has_battery() and charging_status is not None:
            _append_control(buttons, "charge", car.vin,
                            lambda: Switch(CHARGE_SWITCH, car.vin, "Charge", myp.remote_client.charge_now,
                                           charging_status == "InProgress"))

        preconditioning = getattr(status, "preconditionning", None)
        air_conditioning = getattr(preconditioning, "air_conditioning", None)
        preconditioning_status = getattr(air_conditioning, "status", None)
        if preconditioning_status is not None:
            _append_control(buttons, "preconditioning", car.vin,
                            lambda: Switch(PRECONDITIONING_SWITCH, car.vin, "Preconditioning",
                                           myp.remote_client.preconditioning,
                                           preconditioning_status == "Enabled"))

    if not config.offline and car.has_battery() and car.abrp_name:
        _append_control(buttons, "ABRP", car.vin,
                        lambda: Switch(ABRP_SWITCH, car.vin, "Send data to ABRP", myp.abrp.enable_abrp,
                                       car.vin in myp.abrp.abrp_enable_vin))
    return buttons


def get_control_tabs(config):
    if not config.myp.vehicles_list:
        return dbc.Alert("No vehicle is configured.", color="warning")

    tabs = []
    for car in config.myp.vehicles_list:
        label = car.label or car.vin
        myp: PSAClient = config.myp
        status = _get_vehicle_status(myp, car)
        electric_energy = _get_energy(status, "Electric")
        buttons_row = _get_control_buttons(config, myp, car, status, electric_energy)
        content = []
        if buttons_row:
            content.append(dbc.Row(buttons_row))
        if status is None:
            content.append(dbc.Alert("Vehicle status unavailable. Refresh or check API/auth connectivity.",
                                     color="warning"))
        else:
            cards = _get_status_cards(car, status, electric_energy)
            if cards:
                content.append(dbc.Container(dbc.Row(children=create_card(cards)), fluid=True))
        tabs.append(dbc.Tab(label=label, tab_id=car.vin, id="tab-" + car.vin,
                            children=content))
    active_tab = config.myp.vehicles_list[0].vin if config.myp.vehicles_list else None
    return dbc.Tabs(id="control-tabs", active_tab=active_tab, children=tabs)
