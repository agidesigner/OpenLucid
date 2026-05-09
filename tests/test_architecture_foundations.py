import inspect


def test_mcp_run_app_delegates_to_registered_runtime():
    from app import mcp_server
    from app.application import app_runtime

    src = inspect.getsource(mcp_server.run_app)
    assert "run_openlucid_app" in src
    assert "AppRunRequest" in src
    assert "if app_id ==" not in src

    registered = set(app_runtime._APP_ACTIONS)
    assert {
        ("kb_qa", "ask"),
        ("script_writer", "suggest_topic"),
        ("script_writer", "generate"),
        ("content_studio", "generate"),
        ("topic_studio", "generate"),
    }.issubset(registered)


def test_request_detached_work_is_queued_as_task_runs():
    from app.api import assets, offers
    from app.application import task_service

    assert "BackgroundTasks" not in inspect.getsource(assets.upload_asset)
    assert "enqueue_asset_parse" in inspect.getsource(assets.upload_asset)
    assert "enqueue_asset_parse" in inspect.getsource(assets.trigger_parse)
    assert "enqueue_offer_model_inference" in inspect.getsource(offers.create_offer)

    migration = open("alembic/versions/i2x3y4z5a6b7_add_task_runs.py", encoding="utf-8").read()
    assert "task_runs" in migration
    assert "status IN ('pending', 'running')" in migration
    assert "asset.parse" in task_service._HANDLERS
    assert "offer.infer_model" in task_service._HANDLERS


def test_context_pack_exposes_prompt_ready_overlays():
    from app.application import context_pack_service
    from app import mcp_server

    src = inspect.getsource(context_pack_service.build_marketing_context_pack)
    assert "ContextService" in src
    assert "list_memories_for_offer" in src
    assert "render_memories_block" in src
    assert '"prompt_blocks"' in src

    tool_src = inspect.getsource(mcp_server.get_context_pack)
    assert "build_marketing_context_pack" in tool_src
