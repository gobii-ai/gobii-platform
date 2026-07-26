import { chromium } from 'playwright'
const res = await fetch(`https://${process.env.PREVIEW_HOST}/api/v1/browser-session-tickets/`, {
  method:'POST', headers:{'X-Api-Key':process.env.GOBII_PROD_API_KEY,'Content-Type':'application/json'},
  body: JSON.stringify({ expected_environment: process.env.EXPECTED_ENV,
    next_path:`/app/agents/${process.env.AGENT_ID}/`, purpose:'#362 build check' }) })
if(!res.ok){ console.log('TICKET_FAILED',res.status); process.exit(1) }
const b=await chromium.launch(); const c=await b.newContext(); const p=await c.newPage()
await p.goto((await res.json()).login_url,{waitUntil:'domcontentloaded'}); await p.waitForTimeout(6000)
await p.waitForSelector('[data-timeline-item="true"]',{timeout:60000}); await p.waitForTimeout(3000)
const r = await p.evaluate(()=>({
  rows: document.querySelectorAll('[data-timeline-item="true"]').length,
  keyed: document.querySelectorAll('[data-timeline-key]').length,
}))
console.log(`rows=${r.rows} keyedRows=${r.keyed}  ->  ${r.keyed>0 ? 'FIX BUILD IS LIVE' : 'STILL OLD BUILD'}`)
await b.close()
