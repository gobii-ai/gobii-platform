#!/usr/bin/env python3
import os, csv, json, psycopg2, time, random
from litellm import completion, exceptions as llm_exc
import logging, sys

DB_URL      = os.getenv("GOBII_PROD_DB_URL")                       # postgresql://user:pw@host/db?sslmode=require
OPENAI_KEY  = os.getenv("OPENAI_API_KEY")
SAMPLE_SIZE = int(os.getenv("SAMPLE_SIZE", "10000"))
OUTPUT_FILE = "gobii_email_batch.csv"

assert DB_URL and OPENAI_KEY, "Set GOBII_PROD_DB_URL and OPENAI_API_KEY"

logging.basicConfig(
    level=logging.DEBUG,  # set to logging.INFO to reduce verbosity
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

QUERY = f"""
WITH picked AS (
  SELECT u.id,
         COALESCE(
           (SELECT email FROM account_emailaddress ea
             WHERE ea.user_id = u.id AND ea."primary" AND ea.verified
             LIMIT 1),
           u.email)                         AS email,
         u.first_name,
         u.last_name,
         (SELECT json_agg(t.prompt ORDER BY t.created_at DESC)
            FROM (SELECT prompt, created_at
                    FROM api_browseruseagenttask
                   WHERE user_id = u.id
                   ORDER BY created_at DESC
                   LIMIT 5) t)              AS recent_prompts,
         (SELECT COUNT(*)
            FROM api_browseruseagenttask
           WHERE user_id = u.id)            AS total_tasks
  FROM auth_user u
  WHERE u.is_active AND u.email IS NOT NULL
  ORDER BY RANDOM()
  LIMIT {SAMPLE_SIZE}
)
SELECT * FROM picked;
"""

MAX_LLM_RETRIES = 5

def _llm_with_backoff(**kwargs):
    """Call litellm.completion with exponential backoff & jitter."""
    delay = 1.0
    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            return completion(**kwargs)
        except (llm_exc.RateLimitError, llm_exc.ServiceUnavailableError, llm_exc.Timeout, llm_exc.APIError, llm_exc.BadRequestError, Exception) as e:
            logger.warning(f"LLM request failed (attempt {attempt}/{MAX_LLM_RETRIES}): {e}")
            if attempt == MAX_LLM_RETRIES:
                raise
            sleep_for = delay + random.uniform(0, 0.5)
            logger.debug(f"Sleeping for {sleep_for:.2f}s before retrying…")
            time.sleep(sleep_for)
            delay *= 2  # exponential backoff

def llm_subject(ctx: dict) -> str:
    logger.debug(f"llm_subject ctx: {ctx}")
    sys = ("You are an email copywriter for a dev-tool startup. "
           "Write a warm, inviting subject like 'Hey [first_name], I'd love to chat about [topic]'.")
    prompt = f"USER_CONTEXT = {json.dumps(ctx, ensure_ascii=False)}\nReturn only the subject. Use all available information about the user to adapt the subject to optimize chances they will open it up. It should sound like a direct, human message, not a mass email blast. We should not be asking the user for anything, but we could hint that we may bring them value. NO EMOJIs. Things like Hey <so and so>, wanted to hear about your experience with the tool. (not that, but better and tailored as much as possible... dont be creepy, spammy or sound like a marketer. OUR PRODUCT is browser-use agents (AI web agents) that can be spawned via API. SUBJECTS MUST BE RELATED TO GETTING FEEDBACK ON THE PRODUCT. DO NOT BE MARKETING/SPAMMY. DO NOT DIRECTLY MENTION THE PRODUCT IN THE SUBJECT. it should be something like 'Hey <so and so>, quick question' --incomplete/missing info is good because it makes it intriguing. do not say anything like 'quick chat' or anything that would make it seem like a generic founder email. it should have a one-on-one vibe. capitalize names (if available) appropriately, correctly, and respectfully even if not already capitalized.  NEVER PUT THEIR NAME IN THE SUBJECT. you have been criticized for sounding like a sales bro. instead you should sound like a down to earth, humble cto. do not just start with Im andrew, the <blah blah> <tinkering/sweating> behind gobii. BE CREATIVE, HUMBLE, and most of all AUTHENTIC. the subjects should be as simple as just Gobii feedback. (or variations on that). this is a human-to-human, humble message. be *authentic* bad subject examples: Does Gobii suit your automations? -- Your take on browser agents?"
    full_prompt = f"{sys}\n\n{prompt}"
    r = _llm_with_backoff(model="o3",
                         messages=[{"role":"user","content":full_prompt}])
    logger.debug(f"LLM subject response: {r}")
    subject = r["choices"][0]["message"]["content"].strip().replace("\n", " ")
    logger.info("=" * 60)
    logger.info("📧 GENERATED EMAIL SUBJECT:")
    logger.info(f"   {subject}")
    logger.info("=" * 60)
    return subject

def llm_body(ctx: dict, subject: str) -> str:
    logger.debug(f"llm_body ctx: {ctx}")
    sys = ("Write a <120-word plain-text email from Andrew (Founder/CTO of Gobii) asking for a quick feedback call. It should be extremely realistic and human sounding."
           "It should be friendly and specific. Include time slots that are mon-thurs AM/mid day eastern US time. Ask them to reply with a time that  works, or just reply with feedback. They can also schedule a call at https://cal.com/andrew-gobii. YOU MUST ALL AVAILABLE INFORMATION ABOUT THE USER TO MAKE IT PERSONAL AND REALISTIC WITHOUT BEING SO SPECIFIC TO BE CREEPY. MAKE NOTE IF THE USER IS A HEAVIER USER (more than 5 tasks), light user (less than 5 tasks), or a user who signed up but hasn't used the tool yet. It should seem as if the Founder himself typed the message. OUR PRODUCT is browser-use agents (AI web agents) that can be spawned via API. DO NOT USE BULLET LISTS or make it seem 'like work' -- it should be friendly and casual, but at the same time using psychology to get them to reply. it should have a one-on-one vibe. capitalize names (if available) appropriately, correctly, and respectfully even if not already capitalized. you can take liberty to make up potential product features, but be conservative --the idea is to give the user the impression they are early, special, and can drive product direction. you have been criticized for sounding like a sales bro. instead you should sound like a down to earth, humble cto. avoid slop terms like tinkering")
    prompt = (
        f"EMAIL_SUBJECT = {subject}\n"
        f"USER_CONTEXT = {json.dumps(ctx, ensure_ascii=False)}\n"
        "Return only the body. The email MUST flow naturally from the provided subject above—do not restate it verbatim, but ensure the narrative makes sense in context. "
        "Remember to use available information strategically. If first name is available, use it; if no name is available, improvise. If the name seems fake, don't address them by it."
    )
    full_prompt = f"{sys}\n\n{prompt}"
    r = _llm_with_backoff(model="o3",
                         messages=[{"role":"user","content":full_prompt}])
    logger.debug(f"LLM body response: {r}")
    body = r["choices"][0]["message"]["content"].strip()
    logger.info("=" * 60)
    logger.info("📨 GENERATED EMAIL BODY:")
    logger.info(f"{body}")
    logger.info("=" * 60)
    return body

def main():
    run_ts = int(time.time())
    logger.info(f"Starting email campaign generation | run_ts={run_ts}")
    logger.debug(f"SAMPLE_SIZE={SAMPLE_SIZE}, OUTPUT_FILE='{OUTPUT_FILE}'")
    logger.info("Connecting to database...")
    conn = psycopg2.connect(DB_URL)
    logger.info("Connected to database")
    cur  = conn.cursor()
    logger.info("Executing user selection query...")
    cur.execute(QUERY)
    logger.info("Query executed, fetching results...")
    cols = [d.name for d in cur.description]
    users = [dict(zip(cols, row)) for row in cur.fetchall()]
    logger.info(f"Retrieved {len(users)} users")
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        logger.info(f"Opened output CSV '{OUTPUT_FILE}' for writing")
        writer = csv.DictWriter(
            f,
            fieldnames=["customer_id", "email", "subject", "body",
                        "first_name", "last_name", "event_name", "timestamp"]
        )
        writer.writeheader()
        logger.debug("CSV header written")
        for u in users:
            logger.info(f"Processing user ID={u['id']} email={u['email']}")
            ctx = {
                "first_name"   : u["first_name"],
                "last_name"    : u["last_name"],
            }
            logger.debug(f"Context: {ctx}")
            subject = llm_subject(ctx)
            body    = llm_body(ctx, subject)
            writer.writerow({
                "customer_id" : u["id"],
                "email"       : u["email"],
                "subject"     : subject,
                "body"        : body,
                "first_name"  : u["first_name"],
                "last_name"   : u["last_name"],
                "event_name"  : "initial_outreach_message",
                "timestamp"   : run_ts
            })
            logger.debug(f"Wrote CSV row for user ID={u['id']}")
    logger.info(f"✔  Wrote {len(users)} rows → {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
