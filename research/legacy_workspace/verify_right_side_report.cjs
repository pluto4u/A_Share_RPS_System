const { chromium } = require('C:/Users/pluto4u/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
(async () => {
 const browser = await chromium.launch({headless:true, executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const page = await browser.newPage();
 for(const [label,width,height] of [['desktop',1440,1000],['mobile',390,844]]) {
  await page.setViewportSize({width,height});
  await page.goto(pathToFileURL(path.resolve(process.argv[2] || 'output/a_share_analysis_20260907_right_side.html')).href);
  await page.evaluate(()=>scrollTo(0,0));
  const checks=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth, charts:document.querySelectorAll('svg').length, bad:/NaN|Infinity|\{\{/.test(document.body.innerText)}));
  await page.screenshot({path:`output/report_qa_${label}.png`,fullPage:false});
  if(label==='desktop') await page.locator('article').first().screenshot({path:'output/report_qa_chart.png'});
  console.log(label,checks);
  if(checks.overflow||checks.bad||checks.charts!==Number(process.argv[3] || 12)) throw new Error('Report QA failed');
 }
 await browser.close();
})();
