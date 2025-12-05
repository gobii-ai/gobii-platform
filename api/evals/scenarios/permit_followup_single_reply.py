from datetime import timedelta
from datetime import datetime
from django.utils.dateparse import parse_datetime

from django.utils import timezone

from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import register_scenario
from api.models import (
    CommsAllowlistEntry,
    CommsChannel,
    EvalRunTask,
    PersistentAgentCommsSnapshot,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentStepSnapshot,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)
from api.agent.comms.message_service import (
    _ensure_participant,
    _get_or_create_conversation,
    _get_or_create_endpoint,
)
from api.agent.core.event_processing import process_agent_events


@register_scenario
class PermitFollowupSingleReplyScenario(EvalScenario, ScenarioExecutionTools):
    slug = "permit_followup_single_reply"
    description = (
        "Recreates the Carroll Valley permit follow-up prompt that previously triggered duplicate replies. "
        "Agent should send exactly one outbound message."
    )
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_single_reply", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        agent = self.get_agent(agent_id)
        agent_name = (agent.name or "Eval Agent").strip() or "Eval Agent"

        # Seed a completed processing step so the agent does not treat this as first run.
        if not PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent.id, code=PersistentAgentSystemStep.Code.PROCESS_EVENTS
        ).exists():
            prior_step = PersistentAgentStep.objects.create(agent=agent, description="Process events")
            PersistentAgentSystemStep.objects.create(
                step=prior_step,
                code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            )

        # Mock charter and schedule from the original context.
        charter = (
            "I am {name}, a planning assistant helping a couple in Example Borough "
            "(address: 123 Main St, Sampletown, ST 00000) plan a Trex composite deck construction for summer 2026.\n\n"
            "Confirmed project details:\n"
            "- Budget: $20,000–$30,000\n"
            "- Dimensions: Main deck 12x24 ft (288 sf) + elevated walkway 4x24 ft (96 sf) = 384 sf total\n"
            "- Elevation: Approx. 12 ft at house side and up to ~16 ft at far edge; no stairs planned (walkway connects to driveway)\n"
            "- Material selections: Trex Transcend Spiced Rum decking; Trex Signature aluminum railing with charcoal black square balusters; approx. 120 LF of guardrail.\n"
            "- Contractor: Their contractor friend will build; I will support planning, permits, materials, and timeline\n"
            "- Jurisdiction: Example Borough (zoning permit required for any deck; building permit required >30 inches above grade). Frost depth 36\".\n\n"
            "What I’m doing:\n"
            "1) Permitting: Prepare prefilled Zoning Permit and UCC Building Permit application packets; confirm fees, setbacks (R2: F 35', S 15', R 20'; decks may project 3'), lot coverage max 30%, and timelines.\n"
            "2) Budgeting: Maintain detailed line-item budget and materials takeoff; keep total within $20–30k.\n"
            "3) Product research: Track Trex lines, colors, warranties, fasteners, railing, and add-ons.\n"
            "4) Design guidance: Outline framing concept and guard post blocking.\n"
            "5) Zoning checks: Keep record of zoning district (R2) and overlays.\n"
            "6) Timeline & reminders: Maintain a pre-construction schedule with permit lead times and check-ins."
        ).format(name=agent_name)
        updates = {}
        if agent.charter != charter:
            updates["charter"] = charter
        if agent.schedule != "29 15 * * 1":
            updates["schedule"] = "29 15 * * 1"
        if updates:
            for field, value in updates.items():
                setattr(agent, field, value)
            agent.save(update_fields=list(updates.keys()))

        # Ensure summaries from unified history are present via snapshots.
        def _dt_snapshot(s: str):
            return parse_datetime(s) or timezone.now()

        snapshot_until = _dt_snapshot("2025-10-20T15:47:59.501830+00:00")
        step_summary_text = (
            f"{agent_name} is a planning assistant helping a couple at 123 Main St, Sampletown, ST 00000 plan a Trex deck "
            "project. The deck consists of a 12x24 ft main section and a 4x24 ft elevated walkway connector (384 sq ft total), "
            "positioned 12-16 ft above grade near the house. The property is zoned R2 with setbacks of 35ft front, 15ft side, "
            "20ft rear, and 30% max lot coverage.\n\n"
            "Key findings: Example Borough requires a Zoning Permit ($50, <1 week) and Building Permit ($50 base + $5/1000 valuation, "
            "2-4 weeks). Frost depth is 36 inches. Permit applications have been identified and shared with the client.\n\n"
            "Trex material research completed: Enhanced Basics ($4-6/sf), Select ($6-8/sf), Transcend ($8-10/sf). Critical timing: "
            "2025 Trex price increases effective March 31st. Budget estimates created for decking, framing, railings, fasteners, and labor.\n\n"
            "The assistant has established a twice-weekly check-in schedule (Mondays and Thursdays at 3:17 PM), created a SQLite database "
            "to track project info, permit requirements, timeline milestones, research notes, and budget items, and spawned web research "
            "tasks for detailed permit requirements, Trex specifications, and zoning verification. Multiple SMS updates sent to keep "
            "client informed of progress."
        )
        comms_summary_text = (
            "Couple planning a Trex deck at 123 Main St, Sampletown, ST 00000, targeting next summer with a $20–30k budget; a contractor "
            "friend will build it. Design: 12x24 main deck plus a 4x24 walkway to the driveway (total 384 sq ft), second-story height "
            "~12 ft at the house and ~16 ft at the far edge; no stairs—walkway will be elevated on posts due to a steep incline; guardrails "
            "required. R2 setbacks confirmed: front 35 ft, side 15 ft, rear 20 ft.\n\n"
            "Permitting requires a Zoning Permit (~$50) and a UCC Building Permit (~$50–200) due to height >30 inches. Frost depth 36\". "
            "Owner will handle applications but needed reminder of deck dimensions.\n\n"
            "Material selections finalized: Trex Transcend Spiced Rum decking and Signature aluminum square balusters in charcoal black. "
            "Detailed budget provided: $22,000–28,900 total, including materials ($5.4–5.8k decking, $7.2–9.6k railing), pressure-treated "
            "framing ($6–8k), concrete footings ($2–3k), hardware, and permits. Labor estimated separately at $6,000–13,500. Project remains within budget."
        )

        step_snap, created_step_snap = PersistentAgentStepSnapshot.objects.get_or_create(
            agent=agent,
            snapshot_until=snapshot_until,
            defaults={"summary": step_summary_text},
        )
        if not created_step_snap and step_snap.summary != step_summary_text:
            step_snap.summary = step_summary_text
            step_snap.save(update_fields=["summary"])

        comm_snap, created_comm_snap = PersistentAgentCommsSnapshot.objects.get_or_create(
            agent=agent,
            snapshot_until=snapshot_until,
            defaults={"summary": comms_summary_text},
        )
        if not created_comm_snap and comm_snap.summary != comms_summary_text:
            comm_snap.summary = comms_summary_text
            comm_snap.save(update_fields=["summary"])

        agent_sms = "+15550100001"
        user_sms = "+15550100002"
        agent_endpoint = (
            PersistentAgentCommsEndpoint.objects.filter(channel=CommsChannel.SMS, address=agent_sms).first()
            or PersistentAgentCommsEndpoint.objects.create(
                owner_agent=agent, channel=CommsChannel.SMS, address=agent_sms, is_primary=True
            )
        )
        if agent_endpoint.owner_agent_id != agent.id:
            agent_endpoint.owner_agent = agent
            agent_endpoint.is_primary = True
            agent_endpoint.save(update_fields=["owner_agent", "is_primary"])
        if agent.preferred_contact_endpoint_id != agent_endpoint.id:
            agent.preferred_contact_endpoint = agent_endpoint
            agent.save(update_fields=["preferred_contact_endpoint"])

        CommsAllowlistEntry.objects.get_or_create(
            agent=agent,
            channel=CommsChannel.SMS,
            address=user_sms,
            defaults={"is_active": True},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")

        user_endpoint = _get_or_create_endpoint(CommsChannel.SMS, user_sms)
        conv = _get_or_create_conversation(CommsChannel.SMS, user_sms, owner_agent=agent)
        if conv.owner_agent_id != agent.id:
            conv.owner_agent = agent
            conv.save(update_fields=["owner_agent"])
        _ensure_participant(
            conv,
            user_endpoint,
            PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
        )
        _ensure_participant(
            conv,
            agent_endpoint,
            PersistentAgentConversationParticipant.ParticipantRole.AGENT,
        )

        # Seed prior history to mimic the unified history.
        base_time = timezone.now() - timedelta(minutes=12)
        # Seed prior history to mimic the unified history, plus matching tool calls/steps.
        def _dt(s: str) -> datetime:
            return parse_datetime(s) or timezone.now()

        messages_seed = [
            # Browser task results summarized as outbound messages
            (True, "Deck permits: zoning $50; building inspections $5/1000 cu ft ($50 min/$200 max); frost depth 36\"; R2 setbacks 35/15/20; decks can project 3ft.", _dt("2025-10-20T15:47:59.601830+00:00")),
            (True, "Parcel lookup: zoning R2 confirmed; no overlays flagged; R2 dims 35/15/20; lot coverage 30%; projections 3 ft.", _dt("2025-10-20T15:56:02.586601+00:00")),
            (True, "Deck permits for Example Borough: Zoning $50; Building $50-200; frost depth 36\"; R2 setbacks 35/15/20; decks can project 3ft. Want me to prefill the forms?", _dt("2025-10-20T16:00:05.000000+00:00")),
            (True, "Good morning! Following up on my message from last week about prefilling your Example Borough deck permits. Any updates on owner names or your contractor friend's contact? Ready to proceed when you are.", _dt("2025-11-10T15:29:07.227142+00:00")),
            (True, "Good morning! I now have Google Sheets & Trello tools to better organize your deck project. Want me to create a budget spreadsheet or project board? Also any updates on owner names/contractor contact for permits?", _dt("2025-11-17T15:31:47.780376+00:00")),
            (True, "Good morning! Following up on your deck project. Any updates on owner names or your contractor friend's contact for the permit applications? Ready to proceed when you are.", _dt("2025-11-24T15:29:08.925135+00:00")),
            (True, "Great to hear from you! For Example Borough permits you need: 1) Zoning Permit ($50) - any deck 2) Building Permit ($50-200) - required since deck >30\" high. Need owner names, contractor contact (or TBD), confirm $25k value and materials.", _dt("2025-12-04T19:46:35.758868+00:00")),
            (False, 'It needs "description of construction activity". can you suggest something for that', _dt("2025-12-04T19:51:14.963981+00:00")),
            (True, "Suggested description: Construction of a composite deck system with elevated walkway connector. Main deck 12x24 (288 sf); walkway 4x24 (96 sf); total 384 sf; height ~12-16 ft; Trex Transcend Spiced Rum + Signature aluminum rails; connects to driveway; R2 setbacks 35/15/20.", _dt("2025-12-04T19:51:27.830451+00:00")),
            (False, "ok i filled out that form for example borough. now what? do i need the other form?", _dt("2025-12-04T20:29:01.837567+00:00")),
            (True, "Great! Which form did you fill? Zoning ($50) or Building ($50-200)? If zoning is done, I can prefill building if you share owner names, contractor contact (or TBD), and confirm $25k value.", _dt("2025-12-04T20:29:47.932746+00:00")),
            (True, "Which form did you fill? Zoning (Accessory Structure) or Building (Residential Addition/Renovation)? Both required. Share owner names, contractor info, and confirm $25k estimated value so I can prefill building.", _dt("2025-12-04T20:30:17.320775+00:00")),
            (False, "I filled in the Example Borough zoning permit. Where's your source that I need a building permit?", _dt("2025-12-04T20:33:43.359230+00:00")),
            # Additional context messages to match earlier summary
            (True, "Main deck 12x24 (288 sf); walkway 4x24 (96 sf); total 384 sf; height ~12-16 ft; R2 setbacks 35/15/20; decks can project 3 ft; frost depth 36\".", _dt("2025-12-04T19:50:34.806350+00:00")),
        ]

        steps_seed = [
            ("Internal reasoning: send initial update with permit requirements and R2 setbacks.", _dt("2025-10-20T16:00:00.000000+00:00")),
            ("Decided to sleep until next trigger.", _dt("2025-10-20T16:00:10.000000+00:00")),
            ("Cron trigger: 29 15 * * 1", _dt("2025-11-10T15:29:00.218715+00:00")),
            ("Internal reasoning: follow up on permits", _dt("2025-11-10T15:29:04.930210+00:00")),
            ("LLM response issued 1 tool call(s).", _dt("2025-11-10T15:29:09.076406+00:00")),
            ("Decided to sleep until next trigger.", _dt("2025-11-10T15:29:09.539317+00:00")),
            ("Cron trigger: 29 15 * * 1", _dt("2025-11-17T15:31:16.786452+00:00")),
            ("System directive delivered (tool search, reduce browser tasks, tone down schedule)", _dt("2025-11-17T15:31:29.945575+00:00")),
            ("Internal reasoning: search for tools", _dt("2025-11-17T15:31:33.828050+00:00")),
            ("LLM response issued 1 tool call(s).", _dt("2025-11-17T15:32:05.349828+00:00")),
            ("Decided to sleep until next trigger.", _dt("2025-11-17T15:32:05.908439+00:00")),
            ("Cron trigger: 29 15 * * 1", _dt("2025-11-24T15:29:00.190628+00:00")),
            ("System directive delivered (reduce schedule cadence)", _dt("2025-11-24T15:29:00.899115+00:00")),
            ("Internal reasoning: follow up for permit applications", _dt("2025-11-24T15:29:07.497134+00:00")),
            ("System directive delivered (slow schedule, avoid certain browser tasks)", _dt("2025-12-04T19:46:13.202615+00:00")),
            ("System directive delivered (tool search for specific sites)", _dt("2025-12-04T19:46:13.221328+00:00")),
            ("LLM response issued tool call", _dt("2025-12-04T19:46:34.043753+00:00")),
            ("LLM response issued tool call", _dt("2025-12-04T19:50:24.179984+00:00")),
            ("LLM response issued tool call", _dt("2025-12-04T19:50:34.037401+00:00")),
            ("LLM response issued tool call", _dt("2025-12-04T19:51:27.259033+00:00")),
            ("LLM response issued tool call", _dt("2025-12-04T20:29:46.929156+00:00")),
            ("LLM response issued tool call", _dt("2025-12-04T20:30:16.791795+00:00")),
            ("LLM response issued tool call", _dt("2025-12-04T20:31:19.550409+00:00")),
            ("Decided to sleep until next trigger.", _dt("2025-12-04T20:31:19.869856+00:00")),
        ]

        tool_events = [
            (
                "browser_task",
                {"goal": "Extract deck permit requirements, fees, frost depth, zoning constraints"},
                "Completed browser research for permits/fees/zoning",
                _dt("2025-10-20T15:47:59.601830+00:00"),
            ),
            (
                "browser_task",
                {"goal": "Identify zoning district/overlays and dimensional standards"},
                "Completed parcel/zoning lookup",
                _dt("2025-10-20T15:56:02.586601+00:00"),
            ),
            (
                "send_sms",
                {"body": "Deck permits ... want me to prefill?", "to_number": user_sms},
                "SMS queued",
                _dt("2025-10-20T16:00:05.000000+00:00"),
            ),
            (
                "send_sms",
                {"body": "Good morning! Following up on my message from last week about prefilling your Example Borough deck permits. Any updates on owner names or your contractor friend's contact? Ready to proceed when you are.", "to_number": user_sms},
                "SMS queued",
                _dt("2025-11-10T15:29:07.227142+00:00"),
            ),
            (
                "search_tools",
                {"query": "deck planning permit application budgeting construction tools"},
                "Enabled sheets/trello; brightdata already enabled",
                _dt("2025-11-17T15:31:37.253175+00:00"),
            ),
            (
                "send_sms",
                {"body": "Good morning! I now have Google Sheets & Trello tools to better organize your deck project. Want me to create a budget spreadsheet or project board? Also any updates on owner names/contractor contact for permits?", "to_number": user_sms},
                "SMS queued",
                _dt("2025-11-17T15:31:47.780376+00:00"),
            ),
            (
                "send_sms",
                {"body": "Good morning! Following up on your deck project...", "to_number": user_sms},
                "SMS queued",
                _dt("2025-11-24T15:29:08.925135+00:00"),
            ),
            (
                "send_sms",
                {"body": "Great to hear from you! For Example Borough permits you need...", "to_number": user_sms},
                "SMS queued",
                _dt("2025-12-04T19:46:35.758868+00:00"),
            ),
            (
                "sqlite_batch",
                {"operations": ["SELECT * FROM project_info ..."]},
                "error: Database tool is not available on your current plan.",
                _dt("2025-12-04T19:50:24.536436+00:00"),
            ),
            (
                "send_sms",
                {"body": "Main deck: 12x24 ft ... Need any other details?", "to_number": user_sms},
                "SMS queued",
                _dt("2025-12-04T19:50:35.413907+00:00"),
            ),
            (
                "send_sms",
                {"body": "Suggested description ...", "to_number": user_sms},
                "SMS queued",
                _dt("2025-12-04T19:51:28.344298+00:00"),
            ),
            (
                "send_sms",
                {"body": "Great! Which form did you fill out? There are 2 required...", "to_number": user_sms},
                "SMS queued",
                _dt("2025-12-04T20:29:48.934646+00:00"),
            ),
            (
                "send_sms",
                {"body": "Which form did you fill? ...", "to_number": user_sms},
                "SMS queued",
                _dt("2025-12-04T20:30:17.789157+00:00"),
            ),
        ]

        # Combine events to preserve ordering by timestamp.
        combined = []
        for is_outbound, body, ts in messages_seed:
            combined.append(("message", ts, {"is_outbound": is_outbound, "body": body}))
        for desc, ts in steps_seed:
            combined.append(("step", ts, {"description": desc}))
        for tool_name, params, result, ts in tool_events:
            combined.append(("tool", ts, {"tool_name": tool_name, "params": params, "result": result}))

        combined.sort(key=lambda x: x[1])

        msg = None
        for kind, ts, payload in combined:
            if kind == "message":
                created = PersistentAgentMessage.objects.create(
                    is_outbound=payload["is_outbound"],
                    from_endpoint=agent_endpoint if payload["is_outbound"] else user_endpoint,
                    conversation=conv,
                    body=payload["body"],
                    raw_payload={"source": "eval_seed"},
                    owner_agent=agent,
                )
                PersistentAgentMessage.objects.filter(pk=created.pk).update(timestamp=ts)
                if not payload["is_outbound"]:
                    msg = created
            elif kind == "step":
                step = PersistentAgentStep.objects.create(
                    agent=agent,
                    description=payload["description"],
                )
                PersistentAgentStep.objects.filter(pk=step.pk).update(created_at=ts)
            elif kind == "tool":
                step = PersistentAgentStep.objects.create(
                    agent=agent,
                    description=f"Tool call: {payload['tool_name']}({payload['params']})",
                )
                PersistentAgentStep.objects.filter(pk=step.pk).update(created_at=ts)
                PersistentAgentToolCall.objects.create(
                    step=step,
                    tool_name=payload["tool_name"],
                    tool_params=payload["params"],
                    result=payload["result"],
                )

        process_agent_events(str(agent.id), eval_run_id=run_id)

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Injected permit follow-up question after seeding mock context.",
            artifacts={"message": msg},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_single_reply")

        outbound = list(
            PersistentAgentMessage.objects.filter(
                owner_agent_id=agent_id,
                is_outbound=True,
                timestamp__gt=msg.timestamp,
            ).order_by("timestamp")
        )
        outbound_count = len(outbound)

        if outbound_count == 1:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_single_reply",
                observed_summary="Agent sent exactly one reply to the permit follow-up.",
                artifacts={"message": outbound[0]},
            )
            return

        if outbound_count == 0:
            summary = "Agent did not reply to the permit follow-up prompt."
        else:
            summary = f"Agent sent {outbound_count} replies; expected exactly one."

        first = {"message": outbound[0]} if outbound else {}
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_single_reply",
            observed_summary=summary,
            artifacts=first,
        )
