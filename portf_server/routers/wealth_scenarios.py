"""Wealth Scenarios Router — saved Wealth Simulator inputs with tracking."""

import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from portf_manager.services import wealth_scenarios as ws

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


class ScenarioCreate(BaseModel):
    """A new scenario: its name and the simulator inputs."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=500)
    params: dict = Field(default_factory=dict)
    is_primary: bool = False


class ScenarioUpdate(BaseModel):
    """Partial update; new params re-baseline the scenario to today."""

    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=500)
    params: Optional[dict] = None
    is_primary: Optional[bool] = None


def _params_or_422(raw: dict) -> dict:
    try:
        return ws.normalize_params(raw)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


def _view(db, scenario_id: int, with_projection: bool = False) -> dict:
    row = db.get_wealth_scenario(scenario_id)
    if not row:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return ws.scenario_view(
        row, ws.current_net_worth(db), with_projection=with_projection
    )


@router.get("/")
def list_scenarios(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Every saved scenario with its summary and tracking (primary first)."""
    return ws.list_views(db)


@router.post("/", status_code=201)
def create_scenario(
    body: ScenarioCreate,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Save the current inputs under a name; the baseline is today's net worth."""
    params = _params_or_422(body.params)
    try:
        scenario_id = db.create_wealth_scenario(
            name=body.name.strip(),
            description=body.description,
            params=json.dumps(params),
            baseline_date=date.today().isoformat(),
            baseline_net_worth_eur=ws.current_net_worth(db),
            is_primary=body.is_primary,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _view(db, scenario_id)


@router.get("/{scenario_id}")
def get_scenario(
    scenario_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """One scenario with its yearly projection path."""
    return _view(db, scenario_id, with_projection=True)


@router.put("/{scenario_id}")
def update_scenario(
    scenario_id: int,
    body: ScenarioUpdate,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Rename, describe, re-parameterise or promote a scenario.

    Sending ``params`` restarts tracking from today, since the projection
    now starts from new amounts.
    """
    if not db.get_wealth_scenario(scenario_id):
        raise HTTPException(status_code=404, detail="Scenario not found")
    fields: dict = {}
    if body.name is not None:
        fields["name"] = body.name.strip()
    if "description" in body.model_fields_set:
        fields["description"] = body.description
    if body.params is not None:
        fields["params"] = json.dumps(_params_or_422(body.params))
        fields["baseline_date"] = date.today().isoformat()
        fields["baseline_net_worth_eur"] = ws.current_net_worth(db)
    if fields:
        try:
            db.update_wealth_scenario(scenario_id, **fields)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
    if body.is_primary is True:
        db.set_primary_wealth_scenario(scenario_id)
    elif body.is_primary is False:
        current = db.get_wealth_scenario(scenario_id)
        if current and current["is_primary"]:
            db.set_primary_wealth_scenario(None)
    return _view(db, scenario_id)


@router.post("/{scenario_id}/rebaseline")
def rebaseline_scenario(
    scenario_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Restart tracking from today, keeping the inputs as they are."""
    if not db.update_wealth_scenario(
        scenario_id,
        baseline_date=date.today().isoformat(),
        baseline_net_worth_eur=ws.current_net_worth(db),
    ):
        raise HTTPException(status_code=404, detail="Scenario not found")
    return _view(db, scenario_id)


@router.delete("/{scenario_id}")
def delete_scenario(
    scenario_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Delete a scenario."""
    if not db.delete_wealth_scenario(scenario_id):
        raise HTTPException(status_code=404, detail="Scenario not found")
    return {"ok": True}
