const { chromium } = require('C:/Users/pluto4u/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {pathToFileURL}=require('url');const path=require('path');const fs=require('fs');
(async()=>{
 const tag=process.argv[2]||'20261022';if(!/^\d{8}$/.test(tag))throw Error('Expected YYYYMMDD');
 const d=JSON.parse(fs.readFileSync(`output/market_timing_${tag}.json`,'utf8'));
 const b=await chromium.launch({channel:'msedge',headless:true});const p=await b.newPage({viewport:{width:1280,height:950}});let errors=[];p.on('pageerror',e=>errors.push(String(e)));
 await p.goto(pathToFileURL(path.resolve(`output/a_share_analysis_${tag}_market_timing.html`)).href);await p.waitForSelector('#hl-chart svg');
 const n=await p.locator('svg').count(),expected=d.bottom_config?9:7;if(n!==expected)throw Error('Expected '+expected+' charts, got '+n);
 await p.selectOption('#days','120');await p.selectOption('#ma-group','long');await p.selectOption('#hl-period','250');
 if(!(await p.locator('#hl-stats').innerText()).includes(d.latest.low250+'只'))throw Error('Wrong 250-day low count');
 await p.selectOption('#hl-period','20');if(!(await p.locator('#hl-stats').innerText()).includes(d.latest.low20+'只'))throw Error('Wrong 20-day low count');
 await p.selectOption('#days','60');await p.selectOption('#ma-group','short');await p.selectOption('#hl-period','60');
 if(d.bottom_config){await p.selectOption('#bottom-horizon','63');await p.selectOption('#bottom-horizon','20')}
 if(!(await p.locator('#comparison-table').innerText()).includes(d.asof))throw Error('Daily comparison missing');
 await p.screenshot({path:`output/market_timing_${tag}_desktop.png`,fullPage:true});
 await p.screenshot({path:`output/market_timing_${tag}_firstscreen.png`,fullPage:false});
 await p.locator('#comparison').screenshot({path:`output/market_timing_${tag}_comparison.png`});
 await p.locator('#ma').screenshot({path:`output/market_timing_${tag}_ma.png`});
 await p.setViewportSize({width:390,height:844});const overflow=await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth);if(overflow)throw Error('Mobile overflow');
 await p.screenshot({path:`output/market_timing_${tag}_mobile.png`,fullPage:true});
 if(d.latest.up+d.latest.down+d.latest.flat!==d.latest.eligible)throw Error('Population reconciliation failed');
 console.log(JSON.stringify({charts:n,errors,mobileOverflow:overflow,decision:await p.locator('#decision').innerText(),events:d.events.length,confirmed:d.events.filter(e=>e.confirmation==='confirmed').length}));await b.close();if(errors.length)process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1});
