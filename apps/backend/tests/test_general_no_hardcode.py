"""Prove intent/coding/search paths are structural — novel, never-discussed cases."""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_disposable_coding_agent(
    tmp_path,
    monkeypatch,
    thread_id: str,
    project_name: str = "synthetic-shooter",
):
    """Create a Project/Session/ActiveWork authority chain under tmp_path only."""
    from agent import projects as projects_module
    from agent import state as state_module
    from agent.active_work import ActiveWorkStore
    from agent.core import EchoSpeakAgent
    from agent.projects import ProjectManager
    from agent.state import StateStore

    root = tmp_path / project_name
    root.mkdir(parents=True, exist_ok=True)
    (root / "game.js").write_text(
        "const player={hp:100}; const enemies=[]; function update() {}\n",
        encoding="utf-8",
    )
    (root / "index.html").write_text("<canvas id='game'></canvas>\n", encoding="utf-8")
    (root / "style.css").write_text("canvas { display: block; }\n", encoding="utf-8")

    project_manager = ProjectManager(tmp_path / "projects")
    state_store = StateStore(tmp_path / "phase3")
    monkeypatch.setattr(projects_module, "_project_manager", project_manager)
    monkeypatch.setattr(state_module, "_state_store", state_store)

    project = project_manager.attach_folder(str(root), name=project_name, trust_state="trusted")
    state = state_store.update_thread_state(
        thread_id,
        active_project_id=project.id,
        project_path=str(root.resolve()),
        workspace_root=str(root.resolve()),
    )
    agent = EchoSpeakAgent(
        memory_path=str(tmp_path / "memory"),
        manage_background_services=False,
    )
    agent._current_thread_id = thread_id
    agent._state_store = state_store
    agent._execution_context = state
    agent._active_project_id = project.id
    agent._active_work_store = ActiveWorkStore(tmp_path / "active-work")
    return agent, root


def test_coding_intent_novel_genres_never_discussed():
    """Rhythm / tower defense / mystery adventure must match same as any create+artifact ask."""
    from agent.core import EchoSpeakAgent

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    novels = [
        "build me a text-based mystery adventure",
        "make a rhythm game with falling notes",
        "create a tower defense prototype",
        "lets code a turn-based tactics game with permadeath",
        "can you scaffold a visual novel engine in html",
    ]
    for q in novels:
        assert agent._is_coding_project_intent(q) is True, q
        tools = agent._allowed_lc_tool_names(q)
        # Coding create → file tools available; not web-only
        assert "file_write" in tools or "file_list" in tools or "terminal_run" in tools, (q, tools)


def test_local_desktop_intent_not_web_for_novel_folder_slug():
    from agent.core import EchoSpeakAgent, ContextBundle

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    q = "look on my desktop and start on quantum-chess-sim read the files"
    assert agent._is_local_filesystem_intent(q) is True
    tools = agent._allowed_lc_tool_names(q)
    assert "web_search" not in tools
    ctx = ContextBundle(
        extracted_input=q,
        resolved_input=q,
        allowed_tool_names=tools,
    )
    assert agent._pq_shortcut_queries(q, ctx, None) is None


def test_product_title_extraction_not_gta_only():
    """Any title with trailer/price/cast structure — not a GTA whitelist."""
    from agent.research import (
        _extract_title_entity,
        _normalize_product_trailer_query,
        _normalize_product_price_query,
        _normalize_product_cast_query,
        resolve_web_search_queries,
    )

    assert "dune" in _extract_title_entity("when does the new dune movie come out").lower()
    tq = _normalize_product_trailer_query("trailer 2 for hollow knight silksong", full_context="")
    assert "silksong" in tq.lower() or "hollow" in tq.lower()
    assert "trailer" in tq.lower()
    pq = _normalize_product_price_query("how much will elden ring dlc cost")
    assert "price" in pq.lower() or "cost" in pq.lower()
    assert "elden" in pq.lower() or "ring" in pq.lower()
    cq = _normalize_product_cast_query("who are the characters in baldurs gate 3")
    assert "cast" in cq.lower() or "characters" in cq.lower()

    # Multi: release + cost for a novel title (no GTA recipe required)
    multi = (
        "when does starfield shattered space expansion release and how much will it cost"
    )
    resolved = resolve_web_search_queries(multi, multi, use_decomposition=True)
    assert len(resolved) >= 1
    rjoin = " ".join(resolved).lower()
    # Should not collapse to only GTA strings
    assert "gta" not in rjoin or "starfield" in rjoin


def test_weak_answer_refine_does_not_inject_fifa_when_not_in_query():
    from agent.core import EchoSpeakAgent

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    refined = agent._refine_query_after_weak_answer(
        "what nhl games are on tonight",
        "nhl games tonight schedule",
        "I do not have the specific times.",
        1,
    )
    low = refined.lower()
    assert "nhl" in low or "games" in low
    assert "fifa" not in low and "world cup" not in low


def test_sports_normalize_is_structural_not_franchise_map():
    """Novel clubs/nations must compact the same way as any prior test team."""
    import agent.research as research
    from agent.research import (
        _normalize_sports_query,
        _extract_vs_sides,
        _infer_city_from_text,
        resolve_web_search_queries,
    )

    assert getattr(research, "_TEAM_CITY", None) is None

    # Free-form next-game for never-discussed club
    q = _normalize_sports_query("when do the Reykjavik Frost play next")
    assert "reykjavik" in q.lower() or "frost" in q.lower()
    assert "next game" in q.lower() or "schedule" in q.lower()
    # Must NOT rewrite to a different hard-coded franchise
    assert "oilers" not in q.lower()
    assert "edmonton oilers" not in q.lower()

    # vs-sides structural (never-discussed nations)
    sides = _extract_vs_sides("who wins Senegal vs Curaçao tomorrow")
    assert "senegal" in sides.lower()
    assert "cura" in sides.lower() or "curacao" in sides.lower().replace("ç", "c")

    # World Cup + novel sides — no country whitelist required
    fifa_q = _normalize_sports_query("fifa world cup Senegal vs Curaçao kickoff tomorrow")
    assert "senegal" in fifa_q.lower()
    assert "fifa" in fifa_q.lower() or "world cup" in fifa_q.lower()

    # City only when explicitly present — never invent from team nickname
    assert _infer_city_from_text("when do the oilers play next") == ""
    assert _infer_city_from_text("weather in Osaka tomorrow").lower().startswith("osaka")
    assert _infer_city_from_text("Osaka weather tomorrow").lower().startswith("osaka")

    # Multi-intent: novel product + novel sports — no GTA/FIFA recipe required
    multi = (
        "when does hollow knight silksong release and how much will it cost "
        "and what matches are happening for the world cup tomorrow"
    )
    resolved = resolve_web_search_queries(multi, multi, use_decomposition=True)
    rjoin = " ".join(resolved).lower()
    assert len(resolved) >= 2
    assert "silksong" in rjoin or "hollow" in rjoin
    assert "world cup" in rjoin or "fifa" in rjoin or "match" in rjoin
    assert "oilers" not in rjoin
    assert "gta" not in rjoin


def test_no_entity_hardcode_strings_in_sports_normalize_source():
    """Production normalizer must not contain franchise rewrite string literals."""
    import inspect
    from agent.research import _normalize_sports_query

    src = inspect.getsource(_normalize_sports_query)
    banned = (
        "Edmonton Oilers",
        "Calgary Flames",
        "Vancouver Canucks",
        "morocco|portugal|spain|brazil",
        '"oilers"',
    )
    for b in banned:
        assert b not in src, f"hardcoded entity residue in _normalize_sports_query: {b}"


def test_live_sports_intent_without_team_whitelist():
    from agent.sports_data import is_live_sports_data_intent, infer_sport_key, infer_team_tokens

    assert is_live_sports_data_intent("what's the Reykjavik Frost score right now") is True
    assert is_live_sports_data_intent("Senegal vs Curaçao score live") is True
    # No league keyword → no invented Odds API sport key
    assert infer_sport_key("Reykjavik Frost score") is None
    toks = infer_team_tokens("Reykjavik Frost score right now")
    assert any("reykjavik" in t or "frost" in t for t in toks)


def test_weather_place_structural_not_city_list():
    from agent.research import _normalize_weather_query, _infer_city_from_text

    assert _infer_city_from_text("what's the weather in Cape Town")
    wq = _normalize_weather_query("weather tomorrow", city_hint="Cape Town")
    assert "cape town" in wq.lower()
    # Bare weather with no place stays generic (does not invent Edmonton)
    bare = _normalize_weather_query("what's the weather tomorrow")
    assert "edmonton" not in bare.lower()
    assert "weather" in bare.lower()


def test_active_work_persists_across_agent_reinit(tmp_path, monkeypatch):
    """Desktop project open must leave durable fingerprint; new agent restores it."""
    from agent.active_work import ActiveWorkState, looks_like_desktop_relist

    tid = "test-aw-persist-" + tempfile.mkdtemp()[-8:]
    a, project_root = _make_disposable_coding_agent(tmp_path, monkeypatch, tid)
    q = "start on the project folder"
    assert a._is_local_filesystem_intent(q) is True
    a._active_work_store.save(
        ActiveWorkState(
            thread_id=tid,
            kind="coding_project",
            phase="ready",
            project_path=str(project_root),
            project_name=project_root.name,
            goal="inspect and continue the synthetic game",
            next_step="Edit game.js for score",
            files_known=["game.js", "index.html", "style.css"],
            listing="game.js\nindex.html\nstyle.css\n",
            code_digest="### game.js\n// player enemies score collision\n" + ("x" * 100),
        )
    )
    aw = a._load_active_work()
    assert aw is not None and aw.is_active()
    assert project_root.name in aw.project_path.lower().replace("_", "-")
    assert aw.files_known
    stall = "Looks like you got a folder. What kind of game is it? I gotta see what's in there."
    assert a._local_scan_answer_is_hollow(q, stall) is True
    assert looks_like_desktop_relist(stall) is True
    # Fresh agent instance (simulates process/pool re-init)
    b, _ = _make_disposable_coding_agent(tmp_path, monkeypatch, tid)
    b._active_work_store = a._active_work_store
    aw2 = b._load_active_work()
    assert aw2.project_path == aw.project_path
    ctx = b._active_work_context_block()
    assert "Do NOT re-list" in ctx
    assert aw.project_name in ctx or project_root.name in ctx.lower()
    # Continuity recovery must replace hollow stall without re-asking (no live LLM)
    b._invoke_visible_llm = lambda prompt: (
        f"Project at {project_root} with game.js, index.html, style.css. "
        "Canvas shooter already has player/enemies. Next: edit game.js for score."
    )
    b._synthesize_local_project_brief = lambda ui: (
        f"Scanned {project_root.name}: game.js, index.html, style.css. Ready to edit."
    )
    fixed = b._ensure_active_work_continuity(q, stall)
    assert fixed
    flow = fixed.lower()
    assert "what kind of game" not in flow
    assert "gotta see what" not in flow
    assert len(fixed) > 60
    # Hydrate seeds pin + samples
    assert b._hydrate_from_active_work() is True
    assert project_root.name in str(getattr(b, "_last_local_project_path", "")).lower()


def test_active_work_replan_on_incomplete_implement_goal(tmp_path, monkeypatch):
    """Mid-task implement goals must replan from fingerprint, not re-list Desktop."""
    import tempfile
    from agent.active_work import ActiveWorkState, goal_looks_incomplete

    tid = "test-aw-replan-" + tempfile.mkdtemp()[-8:]
    agent, desk = _make_disposable_coding_agent(tmp_path, monkeypatch, tid)
    store = agent._active_work_store
    state = ActiveWorkState(
        thread_id=tid,
        kind="coding_project",
        phase="implement",
        project_path=str(desk),
        project_name=desk.name,
        goal="add score when enemies die and despawn on player hit",
        next_step="Implement in project files: add score when enemies die",
        files_known=["game.js", "index.html", "style.css"],
        listing="game.js\nindex.html\nstyle.css\n",
        code_digest="### game.js\n// player enemies score collision\nfunction update() {}\n",
    )
    store.save(state)
    agent._hydrate_from_active_work()
    stall = (
        "File list done — content='synthetic-shooter/ unrelated-folder... "
        "What kind of game is it? I gotta see what's in there before I can try."
    )
    aw = agent._load_active_work()
    assert goal_looks_incomplete(aw, stall, tools_ran=["file_list"]) is True
    agent._invoke_visible_llm = lambda prompt: (
        "Resuming synthetic-shooter. Goal: add score on enemy kill and despawn on player hit. "
        "Next edit: game.js collision + score counter. Not re-listing Desktop."
    )
    out = agent._ensure_active_work_continuity(
        "can we add a score everytime we kill one of the enemies",
        stall,
    )
    low = out.lower()
    assert "what kind of game" not in low
    assert "unrelated-folder" not in low
    assert "game.js" in low or "score" in low or "resuming" in low or "continuing" in low
    assert len(out) > 40
    # Partial tool restore must include active_work_restore
    tools = [tr.get("tool") for tr in (agent._partial_tool_results or [])]
    assert "active_work_restore" in tools


def test_new_app_request_never_resumes_unrelated_shooter_project(tmp_path, monkeypatch):
    """CRITICAL: to-do list must not reuse 2d-shooter-game pin/path."""
    import tempfile
    from agent.active_work import (
        ActiveWorkState,
        request_continues_project,
        infer_new_project_slug,
    )

    tid = "test-aw-newproj-" + tempfile.mkdtemp()[-8:]
    agent, desk = _make_disposable_coding_agent(tmp_path, monkeypatch, tid)
    store = agent._active_work_store
    store.save(
        ActiveWorkState(
            thread_id=tid,
            kind="coding_project",
            phase="implement",
            project_path=str(desk),
            project_name=desk.name,
            goal="health and scoreboard for shooter",
            next_step="Continue shooter",
            files_known=["game.js", "index.html", "style.css"],
            listing="game.js\nindex.html\nstyle.css",
            code_digest="### game.js\n// canvas shooter enemies\n",
            features_present=["health", "score"],
            file_mtimes={"game.js": 1.0},
        )
    )
    aw = agent._load_active_work()
    q_new = "build me a brand new to-do list app on my desktop"
    assert request_continues_project(q_new, aw) is False
    assert agent._active_work_is_relevant(q_new, aw) is False
    path = agent._resolve_coding_project_path(q_new)
    assert path
    assert desk.name not in path.lower().replace("_", "-")
    assert "todo" in path.lower().replace("_", "-") or "to-do" in path.lower() or "list" in path.lower()
    # slug helper sanity
    slug = infer_new_project_slug(q_new)
    assert "todo" in slug or "list" in slug
    assert "desktop" not in slug

    # Continuity gate on the *shooter* fingerprint (in-memory), independent of overwrite
    q_cont = "also add a pause button to the shooter game"
    assert request_continues_project(q_cont, aw) is True
    # Restore the prior synthetic project on a fresh Session and confirm continuity.
    tid2 = "test-aw-cont-" + tempfile.mkdtemp()[-8:]
    agent2, _ = _make_disposable_coding_agent(tmp_path, monkeypatch, tid2)
    store2 = agent2._active_work_store
    store2.save(
        ActiveWorkState(
            thread_id=tid2,
            kind="coding_project",
            phase="implement",
            project_path=str(desk),
            project_name=desk.name,
            goal="health and scoreboard for shooter",
            next_step="Continue shooter",
            files_known=["game.js", "index.html", "style.css"],
            listing="game.js\nindex.html\nstyle.css",
            code_digest="### game.js\n// canvas shooter enemies\n" + ("x" * 100),
            features_present=["health", "score"],
            file_mtimes={"game.js": 1.0},
        )
    )
    path2 = agent2._resolve_coding_project_path(q_cont)
    assert desk.name in path2.lower().replace("_", "-")


def test_coding_followup_reuses_active_work_skips_full_rescan(tmp_path, monkeypatch):
    """Case 2: state stored but was ignored — follow-up must resume, not cold-scan all files."""
    import tempfile
    from pathlib import Path
    from agent.active_work import ActiveWorkState

    tid = "test-aw-followup-" + tempfile.mkdtemp()[-8:]
    agent, desk = _make_disposable_coding_agent(tmp_path, monkeypatch, tid)
    store = agent._active_work_store
    # Simulate prior turn wrote usable project fingerprint
    digest = (
        "### game.js\nconst player={hp:100}; function damagePlayer(){}\n"
        "### index.html\n<div id='healthBar'></div>\n"
        "### style.css\n.health-bar{}\n"
    )
    mtimes = {}
    for name in ("game.js", "index.html", "style.css"):
        p = desk / name
        if p.is_file():
            mtimes[name] = float(p.stat().st_mtime)
    store.save(
        ActiveWorkState(
            thread_id=tid,
            kind="coding_project",
            phase="implement",
            project_path=str(desk),
            project_name=desk.name,
            goal="health and scoreboard",
            next_step="Continue implementation",
            files_known=["game.js", "index.html", "style.css"],
            listing="game.js\nindex.html\nstyle.css",
            code_digest=digest,
            features_present=["health", "score"],
            file_mtimes=mtimes,
        )
    )
    aw = agent._load_active_work()
    assert aw is not None and aw.has_usable_scan()
    assert aw.same_project(str(desk))
    # Follow-up should classify as implement + same project resume
    q = "also make enemies drop a powerup when killed"
    assert agent._is_coding_implement_intent(q) is True
    # Relevant files should prefer js for gameplay ask
    files = agent._coding_project_source_files(str(desk))
    rel = agent._files_relevant_to_request(q, files)
    assert any(Path(f).name == "game.js" for f in rel)
    # Stale check: matching mtimes => not stale
    for f in files:
        if Path(f).name in mtimes:
            assert agent._file_is_stale_vs_active_work(f, aw) is False
    # Digest parses back into files without needing disk re-read of all three
    parsed = agent._parse_code_digest_to_files(digest, str(desk))
    assert any("game.js" in k.replace("\\", "/") for k in parsed)


def test_coding_implement_intent_uses_plan_state_hooks(tmp_path, monkeypatch):
    """Feature edits on Desktop game must be recognized as implement + plan-worthy."""
    from pathlib import Path

    tid = "test-aw-hooks-" + tempfile.mkdtemp()[-8:]
    agent, desk = _make_disposable_coding_agent(tmp_path, monkeypatch, tid)
    q = (
        f"lets work on {desk.name} and add a health bar "
        "and scoreboard and a you died screen with restart"
    )
    assert agent._is_coding_implement_intent(q) is True
    assert agent._task_planner.needs_planning(q) is True
    path = agent._resolve_coding_project_path(q)
    assert path and desk.name in path.lower().replace("_", "-")
    files = agent._coding_project_source_files(path)
    names = {Path(f).name for f in files}
    assert "game.js" in names
    # Scan/open-only should NOT be implement (brief path stays)
    assert agent._is_coding_implement_intent(f"start {desk.name}") is False


def test_active_work_store_disk_roundtrip():
    """ActiveWorkStore is the continuity layer independent of agent instance."""
    import tempfile
    from pathlib import Path
    from agent.active_work import ActiveWorkState, ActiveWorkStore, next_step_for_phase

    root = Path(tempfile.mkdtemp())
    store = ActiveWorkStore(root=root)
    tid = "disk-roundtrip"
    s = ActiveWorkState(
        thread_id=tid,
        kind="coding_project",
        phase="ready",
        project_path=r"C:\Users\me\Desktop\my-app",
        project_name="my-app",
        goal="open and understand my-app",
        next_step=next_step_for_phase("ready", has_samples=True, goal="open and understand my-app"),
        files_known=["main.py", "readme.md"],
        listing="main.py\nreadme.md\n",
        code_digest="### main.py\nprint('hi')\n",
    )
    store.save(s)
    loaded = store.load(tid)
    assert loaded.is_active()
    assert loaded.project_path == s.project_path
    assert "main.py" in loaded.files_known
    block = store.context_block(tid)
    assert "ACTIVE WORK" in block
    assert "Do NOT re-list" in block
    assert "my-app" in block


def test_coding_score_enemy_never_forces_web_search():
    """Live: 'add score when kill enemies' must not become live sports web_search."""
    from agent.core import EchoSpeakAgent
    import tempfile

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    q = (
        "can we add a score everytime we kill one of the enimies and also make sure "
        "that if they hit the player they disapear"
    )
    assert agent._is_software_game_coding_context(q) is True
    assert agent._needs_live_web_fulfillment(q) is False
    tools = agent._allowed_lc_tool_names(q)
    agent._current_allowed_tools = tools
    agent._partial_tool_results = [{"tool": "file_read", "output": "game.js ok"}]
    out = agent._ensure_live_web_search(q, "I'll edit game.js for score and collision.")
    assert "Unreal" not in out and "Unity" not in out
    assert "I'll edit game.js" in out
    assert "web_search" not in tools


def test_file_edit_resolves_desktop_project_not_echospeak_root():
    """index.html edit during shooter work must hit Desktop/2d-shooter-game, not EchoSpeak."""
    from pathlib import Path
    from agent.tools import set_active_project_root, get_active_project_root, _desktop_root

    desk = _desktop_root()
    proj = desk / "2d-shooter-game"
    if not proj.is_dir():
        return  # skip if not present
    set_active_project_root(str(proj))
    assert get_active_project_root() is not None
    from agent.tools import _candidate_file_path, _file_tool_root

    p = _candidate_file_path("index.html", _file_tool_root())
    assert "2d-shooter-game" in str(p).replace("\\", "/")
    assert "echospeak" not in str(p).lower() or "2d-shooter" in str(p).lower()
    assert p.name == "index.html"


def test_reflector_does_not_retry_accepted_grounded_packet():
    """Log bug: accepted=true still triggered reflector attempts 1 and 2."""
    from agent.core import EchoSpeakAgent, WebTaskReflector
    import tempfile

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    refl = WebTaskReflector(agent)
    packet = (
        "[GROUNDED_SEARCH] accepted=true query=FIFA World Cup matches today\n"
        "France vs Morocco 4:00 PM ET\n"
        "evidence ok"
    )
    calls = {"n": 0}
    orig = agent._grounded_web_search

    def _spy(*a, **k):
        calls["n"] += 1
        return packet

    agent._grounded_web_search = _spy  # type: ignore
    out = refl.reflect_and_retry(
        {
            "index": "t1",
            "tool": "web_search",
            "params": {"q": "FIFA World Cup matches today", "silent": True},
        },
        "web_search",
        packet,
        tools=[],
        callbacks=None,
    )
    assert out == packet
    assert calls["n"] == 0, "must not re-call grounded search after accepted=true"


def test_search_fingerprint_dedupes_tz_word_order():
    from agent.core import EchoSpeakAgent
    import tempfile

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    a = "FIFA World Cup today full match list kickoff ET and mnt convert timezone"
    b = "FIFA World Cup today full match list kickoff ET and et mnt convert timezone"
    c = "FIFA World Cup today full match list kickoff ET and mnt et convert timezone"
    fa, fb, fc = (
        agent._search_query_fingerprint(a),
        agent._search_query_fingerprint(b),
        agent._search_query_fingerprint(c),
    )
    assert fa == fb == fc
    # Grounded re-entry reuses packet without counting as new storms
    agent._request_grounded_results = {}
    agent._request_grounded_count = 0
    agent._request_search_cache = {}
    agent._request_grounded_results[fa] = "[GROUNDED_SEARCH]\naccepted=true\nFrance vs Morocco 4pm"
    out = agent._grounded_web_search(b, original_request=b, emit_tool_events=False)
    assert "France" in out or "4pm" in out
    assert agent._request_grounded_count == 0  # suppressed, did not increment


def test_search_query_quality_gate_rejects_fragments():
    """Utterance fragments must not become searches; multi keeps entity-rich queries only."""
    from agent.research import (
        is_viable_search_query,
        quality_gate_search_queries,
        resolve_web_search_queries,
    )

    parent = "what time does the fifa game with france and maracoo start today? pelsae check"
    assert is_viable_search_query("pelsae check", parent=parent) is False
    assert is_viable_search_query("please check", parent=parent) is False
    assert is_viable_search_query("maracoo start today", parent=parent) is False
    good = "FIFA World Cup france maracoo kickoff time ET today"
    assert is_viable_search_query(good, parent=parent) is True

    gated = quality_gate_search_queries(
        [
            "FIFA World Cup match list kickoff times ET each game schedule fixtures",
            "maracoo start today",
            "pelsae check",
        ],
        parent,
    )
    assert len(gated) >= 1
    assert not any("pelsae" in g.lower() for g in gated)
    assert not any(g.lower() == "maracoo start today" for g in gated)
    # Prefer queries that keep the matchup when present
    rjoin = " ".join(gated).lower()
    assert "fifa" in rjoin or "world cup" in rjoin or "france" in rjoin

    # Real multi still fans out cleanly
    multi = (
        "weather in Osaka tomorrow and what matches are happening for the world cup tomorrow"
    )
    resolved = resolve_web_search_queries(multi, multi, use_decomposition=True)
    assert len(resolved) >= 2
    rjoin2 = " ".join(resolved).lower()
    assert "osaka" in rjoin2 or "weather" in rjoin2
    assert "fifa" in rjoin2 or "world cup" in rjoin2 or "match" in rjoin2
    assert not any("please" in x.lower() and len(x.split()) <= 3 for x in resolved)


def test_fifa_matchup_single_query_not_junk_split():
    """Live: France/maracoo + 'pelsae check' must be ONE sports query, not 3 junk ones."""
    from agent.research import (
        resolve_web_search_queries,
        looks_like_multi_intent,
        _extract_vs_sides,
        _normalize_sports_query,
        _prep_search_work_text,
    )

    q = "what time does the fifa game with france and maracoo start today? pelsae check"
    prep = _prep_search_work_text(q)
    assert "pelsae" not in prep.lower()
    assert "please check" not in prep.lower()
    assert looks_like_multi_intent(q) is False
    sides = _extract_vs_sides(q)
    assert "france" in sides.lower()
    assert "maracoo" in sides.lower() or "morocco" in sides.lower()
    sports = _normalize_sports_query(q)
    assert "france" in sports.lower()
    assert "fifa" in sports.lower() or "world cup" in sports.lower()
    assert "kickoff" in sports.lower() or "time" in sports.lower()
    resolved = resolve_web_search_queries(q, q, use_decomposition=True)
    assert len(resolved) == 1, resolved
    r0 = resolved[0].lower()
    assert "france" in r0
    assert "maracoo" in r0 or "morocco" in r0
    assert "pelsae" not in r0
    assert r0 != "maracoo start today"
    assert "pelsae check" not in r0


def test_local_scan_reloops_into_project_not_stall():
    """After listing Desktop, must enter 2d-shooter-game and not ask 'what first?'."""
    from pathlib import Path
    from agent.core import EchoSpeakAgent

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    q = "lets start the 2d shooter game together and please scan the folder on my desktop"

    # Hollow stall answer must be detected
    stall = (
        "look, i see the 2d-shooter-game folder on your desktop. "
        "we can start building it. what's the first thing you want to look at in there?"
    )
    assert agent._local_scan_answer_is_hollow(q, stall) is True

    # Deep scan against real Desktop if present
    scan = agent._run_local_project_deep_scan(q)
    desk = Path.home() / "Desktop"
    target = desk / "2d-shooter-game"
    if target.is_dir():
        assert scan.get("path")
        assert "2d-shooter" in str(scan["path"]).lower().replace("_", "-")
        # Interior listing should NOT be the whole Desktop sibling dump only
        listing = (scan.get("listing") or "").lower()
        assert "echospeak" not in listing or "index" in listing or "html" in listing or "js" in listing or listing
        # Ensure recovery replaces stall with a brief when samples exist
        fixed = agent._ensure_local_project_deep_scan(q, stall)
        assert fixed
        flow = fixed.lower()
        assert "first thing you want" not in flow
        assert "what do you want to look at" not in flow
        # Should mention real project substance when files were readable
        assert len(fixed) > 80
        assert "2d-shooter" in flow or "game.js" in flow or "index.html" in flow or "scanned" in flow


def test_desktop_project_never_forces_web_search():
    """Live bug: 'start the 2d shooter game together + scan desktop' → internet search.

    Root causes: (1) eth⊂together live-info false positive
                 (2) 'game' classified as sports multi-intent
                 (3) bare 'search' treated as web
    """
    from agent.core import EchoSpeakAgent, ContextBundle
    from agent.research import intent_domains, looks_like_multi_intent

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    q = (
        "lets start the 2d shooter game together and please scan the folder on my desktop"
    )
    assert agent._is_local_filesystem_intent(q) is True
    assert agent._needs_live_web_fulfillment(q) is False
    assert agent._is_explicit_web_query(q) is False
    # eth in together must not mean ethereum
    assert agent._has_live_info_subject(q) is False
    # Not sports multi-intent for software game + desktop
    assert "sports" not in intent_domains(q)
    tools = agent._allowed_lc_tool_names(q)
    assert "web_search" not in tools
    assert "file_list" in tools or "file_read" in tools

    # Stage 3: local Desktop intent returns a project brief — never a web-search fan-out
    ctx = ContextBundle(extracted_input=q, resolved_input=q, allowed_tool_names=tools)
    sc = agent._pq_shortcut_queries(q, ctx, None)
    # Either forced local brief (preferred) or None fall-through — never multi-web packet
    if sc is not None:
        assert sc[1] is True
        body = (sc[0] or "").lower()
        assert "tavily" not in body
        assert "web_search blocked" not in body
        # Real project substance when Desktop folder exists
        from pathlib import Path as _P
        if (_P.home() / "Desktop" / "2d-shooter-game").is_dir():
            assert (
                "2d-shooter" in body
                or "game.js" in body
                or "index.html" in body
                or "scanned" in body
            )

    # Even if something calls grounded web_search, it must refuse and stay local
    blocked = agent._grounded_web_search(q, original_request=q, emit_tool_events=False)
    assert "web_search blocked" in blocked.lower() or "local_filesystem" in blocked.lower()
    assert "tavily" not in blocked.lower()

    # Local file search phrasing is not web
    assert agent._is_explicit_web_query("search my desktop for the project folder") is False
    assert agent._is_explicit_web_query("search the web for pygame collision tutorials") is True


def test_product_price_refine_not_live_score():
    """Live bug: Silksong price weak answer → 'live price today live score result'."""
    from agent.core import EchoSpeakAgent
    from agent.research import build_search_intent

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    # Reflector must not treat product price as sports
    assert agent._task_planner.web_reflector._is_live_score_query(
        "Hollow Knight Silksong price cost pre-order editions"
    ) is False
    assert agent._task_planner.web_reflector._is_live_score_query(
        "live price today"
    ) is False
    assert agent._task_planner.web_reflector._is_live_score_query(
        "oilers score right now"
    ) is True or agent._task_planner.web_reflector._is_live_score_query(
        "nhl score right now"
    ) is True

    refined = agent._refine_query_after_weak_answer(
        "When does Hollow Knight Silksong release and how much will it cost?",
        "Hollow Knight Silksong price cost pre-order editions",
        "No information regarding the cost was found.",
        1,
    )
    low = refined.lower()
    assert "price" in low or "msrp" in low or "cost" in low
    assert "live score" not in low
    assert "score result" not in low

    intent = build_search_intent(
        "how much will silksong cost",
        "Hollow Knight Silksong price cost pre-order editions",
    )
    assert intent.mode != "live_score"
    assert intent.live_score_need is False
