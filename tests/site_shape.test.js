const fs=require('fs'), {JSDOM}=require(process.env.JSDOM_PATH||'jsdom');
const html=fs.readFileSync(process.argv[2],'utf8');
const a=html.indexOf('(function siteShape(){'); const b=html.indexOf('\n})();', a)+6;
const block=html.slice(a,b);
const page=`<body><svg>
 <text class="node-label">Fronius · AC</text><text class="node-sub" id="d_totac_src">Fronius 2,646</text>
 <rect id="load_hw" class="loadbox hw on"/><text id="d_hw">≈ 1,100 W</text><text id="b_hw">ON</text><text id="d_hw_today">– kWh today</text><circle id="p_hw" class="particle on"/>
 <rect id="load_ac"/><text class="node-label">🌡️ Air-con</text><text id="d_ac">0 W</text><text id="b_ac">OFF</text><text id="d_ac_today">x</text><circle id="p_ac"/><path id="c_sur_ac"/>
 <rect class="node-tap" onclick="openDetail('ac')"/><rect class="node-tap" onclick="openDetail('hw')"/></svg>
 <div class="card" data-card="ac">Air-Con card</div><div class="metric"><span id="wk_ac">–</span></div>
 <div id="m_pv_sub">DC 0 + Fronius 2,646</div><script>var keep="Fronius in a script must NOT be touched";</script></body>`;
let fails=0; const check=(n,c)=>{ console.log((c?'  ok ':'  FAIL ')+n); if(!c) fails++; };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
  const dom=new JSDOM(page,{runScripts:'outside-only',pretendToBeVisual:true}); const w=dom.window, d=w.document;
  w.BSF_CONFIG={acSolarName:'ABB ×2',hasAirCon:false,hwConnected:false}; w.state={hw:{hwState:'on'}};
  let calls=0; const RealMO=w.MutationObserver; w.MutationObserver=class extends RealMO{ constructor(cb){ super((...x)=>{calls++; cb(...x);}); } };
  w.eval('var state=window.state;'+block);
  await sleep(50);
  const vis=id=>d.getElementById(id).style.display!=='none';
  check('every visible "Fronius" renamed', !/Fronius|FRONIUS/.test(d.body.textContent.replace(/var keep=.*?;/,'')) && d.getElementById('m_pv_sub').textContent==='DC 0 + ABB ×2 2,646');
  check('text inside <script> left alone', d.querySelector('script').textContent.includes('Fronius in a script'));
  check('air-con box, badge, particle, wire, tap target, card and weekly tile all hidden', !vis('load_ac')&&!vis('d_ac')&&!vis('b_ac')&&!vis('p_ac')&&!vis('c_sur_ac')&&d.querySelector('[data-card="ac"]').style.display==='none'&&d.getElementById('wk_ac').closest('.metric').style.display==='none'&&d.querySelector("rect[onclick*=\"'ac'\"]").style.display==='none');
  check('hot-water tap target NOT hidden', d.querySelector("rect[onclick*=\"'hw'\"]").style.display!=='none');
  check('hot water says not connected / WOULD RUN, never ON or watts', d.getElementById('d_hw').textContent==='not connected' && d.getElementById('b_hw').textContent==='WOULD RUN' && !d.getElementById('load_hw').classList.contains('on') && !d.getElementById('p_hw').classList.contains('on'));
  // the page redraws every tick: simulate 20 redraws that put the false claims back
  for(let i=0;i<20;i++){ d.getElementById('d_hw').textContent='≈ 1,100 W'; d.getElementById('b_hw').textContent='ON'; d.getElementById('load_hw').classList.add('on'); d.getElementById('d_totac_src').textContent='Fronius '+(2600+i); await sleep(10); }
  await sleep(80);
  check('after 20 redraws the truth still stands', d.getElementById('d_hw').textContent==='not connected' && d.getElementById('b_hw').textContent==='WOULD RUN' && !d.getElementById('load_hw').classList.contains('on') && d.getElementById('d_totac_src').textContent==='ABB ×2 2619');
  const before=calls; await sleep(600); const idle=calls-before;
  check('observer SETTLES when the page is idle (no runaway loop): '+idle+' callbacks in 600 ms of quiet', idle<=2);
  check('total observer callbacks stayed proportional to redraws: '+calls, calls<200);
  w.state.hw.hwState='off'; d.getElementById('d_hw').textContent='0 W'; await sleep(60);
  check('when the dispatcher says off it reads WAITING', d.getElementById('b_hw').textContent==='WAITING' && d.getElementById('d_hw').textContent==='not connected');
  // a normal farm-like site must be left completely alone
  const dom2=new JSDOM(page,{runScripts:'outside-only'}); dom2.window.BSF_CONFIG={acSolarName:'Fronius',hasAirCon:true,hwConnected:true}; dom2.window.eval(block); await sleep(40);
  check('a site WITH a Fronius, an air-con and a connected plug is untouched', dom2.window.document.getElementById('d_hw').textContent==='≈ 1,100 W' && dom2.window.document.getElementById('load_ac').style.display!=='none' && /Fronius/.test(dom2.window.document.getElementById('m_pv_sub').textContent));
  console.log('\n'+fails+' failure(s)'); process.exit(fails?1:0);
})();
