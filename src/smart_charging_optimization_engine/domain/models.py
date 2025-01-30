from __future__ import annotations

import itertools
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class FleetPriority(StrEnum):
    critical = "critical"
    high = "high"
    normal = "normal"
    low = "low"


class TimeWindow(BaseModel):
    start_slot: int = Field(ge=0)
    end_slot: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> TimeWindow:
        if self.end_slot <= self.start_slot:
            msg = "end_slot must be strictly greater than start_slot"
            raise ValueError(msg)
        return self


class Charger(BaseModel):
    charger_id: str = Field(min_length=1)
    max_power_kw: float = Field(gt=0.0)
    efficiency: float = Field(default=0.95, gt=0.0, le=1.0)
    site_id: str = Field(default="default", min_length=1)


class Vehicle(BaseModel):
    vehicle_id: str = Field(min_length=1)
    battery_capacity_kwh: float = Field(gt=0.0)
    initial_energy_kwh: float = Field(ge=0.0)
    target_energy_kwh: float = Field(ge=0.0)
    minimum_energy_kwh: float = Field(default=0.0, ge=0.0)
    max_charging_power_kw: float = Field(gt=0.0)
    availability_windows: list[TimeWindow] = Field(min_length=1)
    departure_slot: int = Field(ge=1)
    priority: FleetPriority = Field(default=FleetPriority.normal)
    compatible_charger_ids: list[str] | None = None
    v2g_enabled: bool = Field(default=False)
    max_discharging_power_kw: float = Field(default=0.0, ge=0.0)
    discharge_efficiency: float = Field(default=0.95, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_energy_levels(self) -> Vehicle:
        if self.initial_energy_kwh > self.battery_capacity_kwh:
            msg = "initial_energy_kwh cannot exceed battery capacity"
            raise ValueError(msg)
        if self.target_energy_kwh > self.battery_capacity_kwh:
            msg = "target_energy_kwh cannot exceed battery capacity"
            raise ValueError(msg)
        if self.minimum_energy_kwh > self.target_energy_kwh:
            msg = "minimum_energy_kwh cannot exceed target_energy_kwh"
            raise ValueError(msg)
        ordered_windows = sorted(self.availability_windows, key=lambda window: window.start_slot)
        for previous, current in itertools.pairwise(ordered_windows):
            if current.start_slot < previous.end_slot:
                msg = "availability_windows cannot overlap"
                raise ValueError(msg)
        if self.compatible_charger_ids is not None and not self.compatible_charger_ids:
            msg = "compatible_charger_ids cannot be an empty list"
            raise ValueError(msg)
        if not self.v2g_enabled and self.max_discharging_power_kw > 0.0:
            msg = "max_discharging_power_kw requires v2g_enabled=true"
            raise ValueError(msg)
        return self


class FleetPriorityRule(BaseModel):
    critical_multiplier: float = Field(default=5.0, ge=1.0)
    high_multiplier: float = Field(default=2.0, ge=1.0)
    normal_multiplier: float = Field(default=1.0, ge=1.0)
    low_multiplier: float = Field(default=0.7, gt=0.0)

    def weight_for(self, priority: FleetPriority) -> float:
        return {
            FleetPriority.critical: self.critical_multiplier,
            FleetPriority.high: self.high_multiplier,
            FleetPriority.normal: self.normal_multiplier,
            FleetPriority.low: self.low_multiplier,
        }[priority]


class SiteProfile(BaseModel):
    site_id: str = Field(default="default", min_length=1)
    time_step_minutes: int = Field(gt=0)
    horizon_slots: int = Field(gt=0)
    power_limit_kw: list[float] = Field(min_length=1)
    electricity_price_per_kwh: list[float] = Field(min_length=1)
    export_limit_kw: list[float] | None = None
    export_price_per_kwh: list[float] | None = None

    @model_validator(mode="after")
    def validate_profile_lengths(self) -> SiteProfile:
        if len(self.power_limit_kw) != self.horizon_slots:
            msg = "power_limit_kw length must match horizon_slots"
            raise ValueError(msg)
        if len(self.electricity_price_per_kwh) != self.horizon_slots:
            msg = "electricity_price_per_kwh length must match horizon_slots"
            raise ValueError(msg)
        if self.export_limit_kw is not None and len(self.export_limit_kw) != self.horizon_slots:
            msg = "export_limit_kw length must match horizon_slots"
            raise ValueError(msg)
        if (
            self.export_price_per_kwh is not None
            and len(self.export_price_per_kwh) != self.horizon_slots
        ):
            msg = "export_price_per_kwh length must match horizon_slots"
            raise ValueError(msg)
        if any(limit < 0.0 for limit in self.power_limit_kw):
            msg = "power_limit_kw values must be non-negative"
            raise ValueError(msg)
        if any(price < 0.0 for price in self.electricity_price_per_kwh):
            msg = "electricity_price_per_kwh values must be non-negative"
            raise ValueError(msg)
        if self.export_limit_kw is not None and any(limit < 0.0 for limit in self.export_limit_kw):
            msg = "export_limit_kw values must be non-negative"
            raise ValueError(msg)
        if self.export_price_per_kwh is not None and any(
            price < 0.0 for price in self.export_price_per_kwh
        ):
            msg = "export_price_per_kwh values must be non-negative"
            raise ValueError(msg)
        return self


class ObjectiveConfig(BaseModel):
    unmet_demand_penalty_per_kwh: float = Field(default=100.0, ge=0.0)
    load_smoothing_penalty_per_kw_change: float = Field(default=0.0, ge=0.0)
    site_demand_charge_per_kw: float = Field(default=0.0, ge=0.0)
    allow_vehicle_to_grid: bool = Field(default=False)
    battery_degradation_cost_per_kwh: float = Field(default=0.0, ge=0.0)


class ScenarioMetadata(BaseModel):
    scenario_name: str = Field(min_length=1)
    description: str = Field(default="")
    scenario_id: str | None = None


class ChargingScenario(BaseModel):
    metadata: ScenarioMetadata
    site: SiteProfile
    chargers: list[Charger] = Field(min_length=1)
    vehicles: list[Vehicle] = Field(min_length=1)
    objective: ObjectiveConfig = Field(default_factory=ObjectiveConfig)
    fleet_priority_rules: FleetPriorityRule = Field(default_factory=FleetPriorityRule)

    @field_validator("vehicles")
    @classmethod
    def validate_unique_vehicle_ids(cls, vehicles: list[Vehicle]) -> list[Vehicle]:
        vehicle_ids = {vehicle.vehicle_id for vehicle in vehicles}
        if len(vehicle_ids) != len(vehicles):
            msg = "vehicle_id values must be unique"
            raise ValueError(msg)
        return vehicles

    @field_validator("chargers")
    @classmethod
    def validate_unique_charger_ids(cls, chargers: list[Charger]) -> list[Charger]:
        charger_ids = {charger.charger_id for charger in chargers}
        if len(charger_ids) != len(chargers):
            msg = "charger_id values must be unique"
            raise ValueError(msg)
        return chargers

    @model_validator(mode="after")
    def validate_temporal_consistency(self) -> ChargingScenario:
        horizon_slots = self.site.horizon_slots
        charger_ids = {charger.charger_id for charger in self.chargers}
        for vehicle in self.vehicles:
            if vehicle.departure_slot > horizon_slots:
                msg = f"Vehicle {vehicle.vehicle_id} departure_slot exceeds horizon"
                raise ValueError(msg)
            if vehicle.compatible_charger_ids is not None:
                unknown_chargers = set(vehicle.compatible_charger_ids) - charger_ids
                if unknown_chargers:
                    msg = (
                        f"Vehicle {vehicle.vehicle_id} references unknown chargers: "
                        f"{sorted(unknown_chargers)}"
                    )
                    raise ValueError(msg)
            for window in vehicle.availability_windows:
                if window.end_slot > horizon_slots:
                    msg = f"Vehicle {vehicle.vehicle_id} availability window exceeds horizon"
                    raise ValueError(msg)
                if window.end_slot > vehicle.departure_slot:
                    msg = (
                        f"Vehicle {vehicle.vehicle_id} has an availability window "
                        "after departure_slot"
                    )
                    raise ValueError(msg)
        if self.objective.allow_vehicle_to_grid:
            charger_by_id = {charger.charger_id: charger for charger in self.chargers}
            for vehicle in self.vehicles:
                if not vehicle.v2g_enabled or vehicle.max_discharging_power_kw <= 0.0:
                    continue
                compatible_ids = vehicle.compatible_charger_ids or list(charger_ids)
                compatible_chargers = [charger_by_id[cid] for cid in compatible_ids]
                max_charger_power = max((c.max_power_kw for c in compatible_chargers), default=0.0)
                if max_charger_power <= 0.0:
                    msg = (
                        f"Vehicle {vehicle.vehicle_id} has V2G enabled but no compatible "
                        "chargers can handle its discharge power"
                    )
                    raise ValueError(msg)
        return self


class TelemetryVehicleState(BaseModel):
    vehicle_id: str = Field(min_length=1)
    observed_energy_kwh: float = Field(ge=0.0)
    connected: bool = Field(default=True)
    connected_charger_id: str | None = None

    @model_validator(mode="after")
    def validate_connection_state(self) -> TelemetryVehicleState:
        if self.connected and self.connected_charger_id is None:
            msg = "connected_charger_id is required when connected=true"
            raise ValueError(msg)
        if not self.connected and self.connected_charger_id is not None:
            msg = "connected_charger_id must be omitted when connected=false"
            raise ValueError(msg)
        return self


class TelemetrySnapshot(BaseModel):
    snapshot_id: str = Field(min_length=1)
    current_slot: int = Field(ge=0)
    vehicle_states: list[TelemetryVehicleState] = Field(min_length=1)
    power_limit_override_kw: list[float] | None = None
    electricity_price_override_per_kwh: list[float] | None = None

    @field_validator("vehicle_states")
    @classmethod
    def validate_unique_vehicle_state_ids(
        cls,
        vehicle_states: list[TelemetryVehicleState],
    ) -> list[TelemetryVehicleState]:
        vehicle_ids = {vehicle_state.vehicle_id for vehicle_state in vehicle_states}
        if len(vehicle_ids) != len(vehicle_states):
            msg = "telemetry vehicle_id values must be unique"
            raise ValueError(msg)
        return vehicle_states

    @model_validator(mode="after")
    def validate_overrides(self) -> TelemetrySnapshot:
        if self.power_limit_override_kw is not None and any(
            value < 0.0 for value in self.power_limit_override_kw
        ):
            msg = "power_limit_override_kw values must be non-negative"
            raise ValueError(msg)
        if self.electricity_price_override_per_kwh is not None and any(
            value < 0.0 for value in self.electricity_price_override_per_kwh
        ):
            msg = "electricity_price_override_per_kwh values must be non-negative"
            raise ValueError(msg)
        return self


class RollingHorizonRequest(BaseModel):
    scenario: ChargingScenario
    telemetry: TelemetrySnapshot
    source_run_id: str | None = None


class TelemetryMessageEnvelope(BaseModel):
    envelope_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    received_at: datetime
    telemetry: TelemetrySnapshot


class CoordinatedSiteScenario(BaseModel):
    site: SiteProfile
    chargers: list[Charger] = Field(min_length=1)
    vehicles: list[Vehicle] = Field(min_length=1)
    objective: ObjectiveConfig = Field(default_factory=ObjectiveConfig)
    fleet_priority_rules: FleetPriorityRule = Field(default_factory=FleetPriorityRule)

    @field_validator("vehicles")
    @classmethod
    def validate_unique_vehicle_ids(cls, vehicles: list[Vehicle]) -> list[Vehicle]:
        vehicle_ids = {vehicle.vehicle_id for vehicle in vehicles}
        if len(vehicle_ids) != len(vehicles):
            msg = "vehicle_id values must be unique within a site"
            raise ValueError(msg)
        return vehicles

    @field_validator("chargers")
    @classmethod
    def validate_unique_charger_ids(cls, chargers: list[Charger]) -> list[Charger]:
        charger_ids = {charger.charger_id for charger in chargers}
        if len(charger_ids) != len(chargers):
            msg = "charger_id values must be unique within a site"
            raise ValueError(msg)
        return chargers

    @model_validator(mode="after")
    def validate_site_consistency(self) -> CoordinatedSiteScenario:
        scenario = ChargingScenario(
            metadata=ScenarioMetadata(scenario_name=self.site.site_id),
            site=self.site,
            chargers=self.chargers,
            vehicles=self.vehicles,
            objective=self.objective,
            fleet_priority_rules=self.fleet_priority_rules,
        )
        expected_site_id = self.site.site_id
        for charger in scenario.chargers:
            if charger.site_id != expected_site_id:
                msg = f"Charger {charger.charger_id} site_id must match site.site_id"
                raise ValueError(msg)
        return self


class NetworkConstraint(BaseModel):
    power_limit_kw: list[float] | None = None
    demand_charge_per_kw: float = Field(default=0.0, ge=0.0)


class PortfolioScenario(BaseModel):
    metadata: ScenarioMetadata
    sites: list[CoordinatedSiteScenario] = Field(min_length=1)
    network: NetworkConstraint = Field(default_factory=NetworkConstraint)

    @model_validator(mode="after")
    def validate_alignment(self) -> PortfolioScenario:
        first_site = self.sites[0].site
        first_objective = self.sites[0].objective
        site_ids = {site.site.site_id for site in self.sites}
        if len(site_ids) != len(self.sites):
            msg = "site_id values must be unique within a portfolio"
            raise ValueError(msg)
        for site in self.sites[1:]:
            if site.site.time_step_minutes != first_site.time_step_minutes:
                msg = "All sites in a portfolio must have the same time_step_minutes"
                raise ValueError(msg)
            if site.site.horizon_slots != first_site.horizon_slots:
                msg = "All sites in a portfolio must have the same horizon_slots"
                raise ValueError(msg)
            if site.objective.allow_vehicle_to_grid != first_objective.allow_vehicle_to_grid:
                msg = "All sites in a portfolio must agree on allow_vehicle_to_grid"
                raise ValueError(msg)
            if (
                site.objective.battery_degradation_cost_per_kwh
                != first_objective.battery_degradation_cost_per_kwh
            ):
                msg = "All sites in a portfolio must use the same battery_degradation_cost_per_kwh"
                raise ValueError(msg)
        if (
            self.network.power_limit_kw is not None
            and len(self.network.power_limit_kw) != first_site.horizon_slots
        ):
            msg = "network.power_limit_kw length must match site horizon_slots"
            raise ValueError(msg)
        if self.network.power_limit_kw is not None and any(
            value < 0.0 for value in self.network.power_limit_kw
        ):
            msg = "network.power_limit_kw values must be non-negative"
            raise ValueError(msg)
        return self
