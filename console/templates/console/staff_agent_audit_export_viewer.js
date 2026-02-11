    function toText(value) {
      if (value === null || value === undefined) return "";
      if (typeof value === "string") return value;
      try {
        return JSON.stringify(value, null, 2);
      } catch (error) {
        return String(value);
      }
    }

    function parseTime(value) {
      const ts = Date.parse(value || "");
      return Number.isNaN(ts) ? 0 : ts;
    }

    function formatTime(value) {
      if (!value) return "(no timestamp)";
      const ts = Date.parse(value);
      if (Number.isNaN(ts)) return value;
      return new Date(ts).toLocaleString();
    }

    function sortByTimestamp(items, order) {
      const direction = order === "asc" ? 1 : -1;
      return items.slice().sort((left, right) => {
        const leftTime = parseTime(left.timestamp);
        const rightTime = parseTime(right.timestamp);
        if (leftTime === rightTime) {
          return String(left.id || "").localeCompare(String(right.id || ""));
        }
        return (leftTime - rightTime) * direction;
      });
    }

    function makePill(text, className) {
      const span = document.createElement("span");
      span.className = className ? "pill " + className : "pill";
      span.textContent = text;
      return span;
    }

    function makePre(label, value) {
      const wrapper = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = label;
      wrapper.appendChild(summary);
      const pre = document.createElement("pre");
      pre.textContent = toText(value);
      wrapper.appendChild(pre);
      return wrapper;
    }

    function makeAlwaysExpandedPre(label, value) {
      const wrapper = document.createElement("div");
      wrapper.style.marginTop = "8px";

      const heading = document.createElement("div");
      heading.textContent = label;
      heading.style.fontSize = "13px";
      heading.style.fontWeight = "600";
      heading.style.color = "#0b1220";
      wrapper.appendChild(heading);

      const pre = document.createElement("pre");
      pre.textContent = toText(value);
      wrapper.appendChild(pre);
      return wrapper;
    }

    async function copyTextToClipboard(text) {
      const content = toText(text);
      if (!content) return false;
      if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
        return false;
      }
      try {
        await navigator.clipboard.writeText(content);
        return true;
      } catch (error) {
        return false;
      }
    }

    function makeCopyablePromptPre(label, value) {
      const wrapper = document.createElement("details");
      const summary = document.createElement("summary");
      const row = document.createElement("span");
      row.className = "summary-row";

      const text = document.createElement("span");
      text.textContent = label;
      row.appendChild(text);

      const copyButton = document.createElement("button");
      copyButton.type = "button";
      copyButton.className = "copy-btn";
      copyButton.textContent = "Copy";
      copyButton.setAttribute("aria-label", "Copy " + label);
      copyButton.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const copied = await copyTextToClipboard(value);
        const resetText = copied ? "Copied" : "Copy failed";
        copyButton.textContent = resetText;
        window.setTimeout(() => {
          copyButton.textContent = "Copy";
        }, 1200);
      });
      row.appendChild(copyButton);

      summary.appendChild(row);
      wrapper.appendChild(summary);

      const pre = document.createElement("pre");
      pre.textContent = toText(value);
      wrapper.appendChild(pre);
      return wrapper;
    }

    function sanitizeHtml(html) {
      if (!html || typeof html !== "string") return "";
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, "text/html");

      doc.querySelectorAll("script,style,iframe,object,embed,link,meta").forEach((node) => node.remove());

      doc.querySelectorAll("*").forEach((el) => {
        Array.from(el.attributes).forEach((attr) => {
          const name = attr.name.toLowerCase();
          const value = (attr.value || "").trim();
          if (name.startsWith("on")) {
            el.removeAttribute(attr.name);
            return;
          }
          if ((name === "href" || name === "src") && value.toLowerCase().startsWith("javascript:")) {
            el.removeAttribute(attr.name);
          }
        });
      });

      return doc.body.innerHTML || "";
    }

    function looksLikeHtml(value) {
      if (!value || typeof value !== "string") return false;
      const text = value.trim();
      if (!text) return false;
      return /<\/?[a-z][\w:-]*\b[^>]*>/i.test(text);
    }

    function renderCounts(counts, total) {
      const container = document.getElementById("timeline-counts");
      container.innerHTML = "";
      container.appendChild(makePill("Total: " + total));
      container.appendChild(makePill("Completions: " + (counts.completions || 0), "completion"));
      container.appendChild(makePill("Messages: " + (counts.messages || 0), "message"));
    }

    function renderCompletionCard(item) {
      const completion = item.data;
      const card = document.createElement("article");
      card.className = "card completion";

      const head = document.createElement("div");
      head.className = "card-head";

      const title = document.createElement("h3");
      title.className = "card-title";
      const model = completion.llm_model || "Unknown model";
      const provider = completion.llm_provider || "provider";
      title.textContent = "Completion - " + model + " (" + provider + ")";
      head.appendChild(title);

      const stamp = document.createElement("p");
      stamp.className = "stamp";
      stamp.textContent = formatTime(completion.timestamp);
      head.appendChild(stamp);
      card.appendChild(head);

      const meta = document.createElement("div");
      meta.className = "meta";
      meta.appendChild(makePill("Completion", "completion"));
      meta.appendChild(makePill("Type: " + (completion.completion_type || "other"), "completion"));
      if (completion.prompt_tokens !== null && completion.prompt_tokens !== undefined) {
        meta.appendChild(makePill("Prompt: " + completion.prompt_tokens));
      }
      if (completion.completion_tokens !== null && completion.completion_tokens !== undefined) {
        meta.appendChild(makePill("Output: " + completion.completion_tokens));
      }
      if (completion.total_tokens !== null && completion.total_tokens !== undefined) {
        meta.appendChild(makePill("Total: " + completion.total_tokens));
      }
      if (completion.response_id) {
        meta.appendChild(makePill("Response: " + completion.response_id));
      }
      card.appendChild(meta);

      const promptArchive = completion.prompt_archive || null;
      const promptPayload = promptArchive && promptArchive.payload ? promptArchive.payload : null;
      if (promptPayload) {
        if (promptPayload.system_prompt) {
          card.appendChild(makeCopyablePromptPre("System prompt", promptPayload.system_prompt));
        }
        if (promptPayload.user_prompt) {
          card.appendChild(makeCopyablePromptPre("User prompt", promptPayload.user_prompt));
        }
        if (promptPayload.error) {
          const warn = document.createElement("div");
          warn.className = "warn";
          warn.textContent = "Prompt payload unavailable: " + promptPayload.error;
          card.appendChild(warn);
        }
      }

      if (completion.thinking) {
        card.appendChild(makePre("Thinking content", completion.thinking));
      }

      const toolCalls = Array.isArray(completion.tool_calls) ? completion.tool_calls : [];
      if (toolCalls.length) {
        const sortedTools = sortByTimestamp(toolCalls, document.getElementById("sort-order").value || "desc");
        sortedTools.forEach((tool) => {
          const toolCard = document.createElement("section");
          toolCard.className = "tool-call";

          const toolName = document.createElement("h4");
          toolName.textContent = tool.tool_name || "Tool call";
          toolCard.appendChild(toolName);

          const toolTime = document.createElement("p");
          toolTime.textContent = formatTime(tool.timestamp);
          toolCard.appendChild(toolTime);

          const toolMeta = document.createElement("div");
          toolMeta.className = "meta";
          toolMeta.appendChild(makePill("Tool call", "tool"));
          if (tool.execution_duration_ms !== null && tool.execution_duration_ms !== undefined) {
            toolMeta.appendChild(makePill("Duration: " + tool.execution_duration_ms + " ms", "tool"));
          }
          toolCard.appendChild(toolMeta);

          toolCard.appendChild(makePre("Parameters", tool.parameters));
          toolCard.appendChild(makePre("Result", tool.result));

          card.appendChild(toolCard);
        });
      }

      return card;
    }

    function renderMessageCard(item) {
      const message = item.data;
      const card = document.createElement("article");
      card.className = "card message";

      const head = document.createElement("div");
      head.className = "card-head";

      const title = document.createElement("h3");
      title.className = "card-title";
      const direction = message.is_outbound ? "Message - Agent to User" : "Message - User to Agent";
      title.textContent = direction;
      head.appendChild(title);

      const stamp = document.createElement("p");
      stamp.className = "stamp";
      stamp.textContent = formatTime(message.timestamp);
      head.appendChild(stamp);

      card.appendChild(head);

      const meta = document.createElement("div");
      meta.className = "meta";
      meta.appendChild(makePill("Message", "message"));
      meta.appendChild(makePill("Channel: " + (message.channel || "web"), "message"));
      if (message.id) {
        meta.appendChild(makePill("Message: " + message.id, "message"));
      }
      card.appendChild(meta);

      const htmlBody = typeof message.body_html === "string" ? message.body_html.trim() : "";
      const bodyText = typeof message.body_text === "string" ? message.body_text : "";
      const htmlCandidate = htmlBody || (looksLikeHtml(bodyText) ? bodyText : "");
      if (htmlCandidate) {
        const bodyWrapper = document.createElement("div");
        bodyWrapper.style.marginTop = "8px";

        const heading = document.createElement("div");
        heading.textContent = "Body";
        heading.style.fontSize = "13px";
        heading.style.fontWeight = "600";
        heading.style.color = "#0b1220";
        bodyWrapper.appendChild(heading);

        const body = document.createElement("div");
        body.className = "html-body";
        body.innerHTML = sanitizeHtml(htmlCandidate);
        bodyWrapper.appendChild(body);
        card.appendChild(bodyWrapper);
      } else {
        if (bodyText) {
          card.appendChild(makeAlwaysExpandedPre("Body", bodyText));
        }
      }

      const attachments = Array.isArray(message.attachments) ? message.attachments : [];
      if (attachments.length) {
        const attachmentLines = attachments.map((attachment) => {
          const name = attachment.filespace_path || attachment.filename || "attachment";
          const size = attachment.file_size_label ? " (" + attachment.file_size_label + ")" : "";
          return name + size;
        });
        card.appendChild(makeAlwaysExpandedPre("Attachments", attachmentLines.join("\n")));
      }

      return card;
    }

    function buildTimelineItems(data) {
      const items = [];
      const completions = Array.isArray(data.completions) ? data.completions : [];
      const messages = Array.isArray(data.messages) ? data.messages : [];

      completions.forEach((completion) => {
        items.push({
          kind: "completion",
          id: completion.id,
          timestamp: completion.timestamp,
          data: completion,
        });
      });

      messages.forEach((message) => {
        items.push({
          kind: "message",
          id: message.id,
          timestamp: message.timestamp,
          data: message,
        });
      });

      return items;
    }

    function renderTimeline(data) {
      const list = document.getElementById("timeline-list");
      list.innerHTML = "";

      const order = (document.getElementById("sort-order").value || "desc");
      const items = sortByTimestamp(buildTimelineItems(data), order);

      renderCounts(data.counts || {}, items.length);

      if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "empty";
        empty.textContent = "No completion or message events found in this export.";
        list.appendChild(empty);
        return;
      }

      items.forEach((item) => {
        if (item.kind === "completion") {
          list.appendChild(renderCompletionCard(item));
        } else if (item.kind === "message") {
          list.appendChild(renderMessageCard(item));
        }
      });
    }

    const auditData = window.__AUDIT_DATA__;
    if (!auditData || typeof auditData !== "object") {
      const timeline = document.getElementById("timeline-list");
      if (timeline) {
        timeline.innerHTML = "";
        const empty = document.createElement("p");
        empty.className = "empty";
        empty.textContent = "Unable to load audit data. Ensure audit-data.js is in the same folder as index.html.";
        timeline.appendChild(empty);
      }
      throw new Error("Audit data unavailable");
    }

    const summary = document.getElementById("summary-line");
    if (summary) {
      const exportedAt = formatTime(auditData.exported_at);
      const completionCount = auditData.counts && auditData.counts.completions ? auditData.counts.completions : 0;
      const messageCount = auditData.counts && auditData.counts.messages ? auditData.counts.messages : 0;
      summary.textContent = "Generated " + exportedAt + " - " + completionCount + " completions - " + messageCount + " messages";
    }

    renderTimeline(auditData);

    const orderSelect = document.getElementById("sort-order");
    if (orderSelect) {
      orderSelect.addEventListener("change", () => renderTimeline(auditData));
    }
