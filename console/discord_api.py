"""Console endpoints for native Discord bot OAuth."""

import json

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.views import View

from api.models import PersistentAgentDiscordOAuthSession
from api.services.discord_bot import (
    DiscordBotIntegrationError,
    handle_discord_oauth_callback,
    start_discord_oauth,
)
from console.agent_chat.access import resolve_manageable_agent_for_request
from console.api_helpers import ApiLoginRequiredMixin


def _discord_oauth_complete_response(*, agent_id: str, guild_count: int) -> HttpResponse:
    payload = json.dumps(
        {
            "type": "gobii:discord_oauth_complete",
            "status": "success",
            "agent_id": agent_id,
            "guild_count": guild_count,
        }
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Discord Connected</title>
</head>
<body>
  <p id="status">Discord connected. This tab will close automatically.</p>
  <button type="button" id="close-button">Close tab</button>
  <script>
    (function() {{
      var payload = {payload};
      function closeTab() {{
        window.close();
        window.setTimeout(function() {{
          document.getElementById("status").textContent = "Discord connected. You can close this tab.";
        }}, 500);
      }}
      if (window.opener && !window.opener.closed) {{
        window.opener.postMessage(payload, window.location.origin);
      }}
      document.getElementById("close-button").addEventListener("click", closeTab);
      closeTab();
    }}());
  </script>
</body>
</html>"""
    return HttpResponse(html, content_type="text/html")


class DiscordOAuthStartView(ApiLoginRequiredMixin, View):
    def get(self, request):
        agent_id = str(request.GET.get("agent_id") or "").strip()
        if not agent_id:
            return HttpResponseBadRequest("agent_id is required.")
        try:
            agent = resolve_manageable_agent_for_request(request, agent_id)
            return HttpResponseRedirect(start_discord_oauth(agent, request.user))
        except PermissionDenied:
            return JsonResponse({"error": "Not permitted to manage this agent."}, status=403)
        except DiscordBotIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)


class DiscordOAuthCallbackView(ApiLoginRequiredMixin, View):
    def get(self, request):
        error = str(request.GET.get("error") or "").strip()
        if error:
            return JsonResponse({"error": error}, status=400)
        state = str(request.GET.get("state") or "").strip()
        code = str(request.GET.get("code") or "").strip()
        if not state or not code:
            return HttpResponseBadRequest("state and code are required.")
        try:
            session = PersistentAgentDiscordOAuthSession.objects.select_related("agent").get(state=state)
            agent_id = str(session.agent_id)
            resolve_manageable_agent_for_request(request, agent_id)
            result = handle_discord_oauth_callback(state=state, code=code)
        except PersistentAgentDiscordOAuthSession.DoesNotExist:
            return JsonResponse({"error": "Discord authorization state was not found."}, status=404)
        except PermissionDenied:
            return JsonResponse({"error": "Not permitted to manage this agent."}, status=403)
        except DiscordBotIntegrationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        return _discord_oauth_complete_response(agent_id=agent_id, guild_count=result.claimed_count)
