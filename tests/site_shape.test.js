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
 <div id="m_pv_sub">DC 0 + Fronius 2,646</div><div id="en_cards"><div>SOLAR 0.3</div><div>HOT WATER 0.0</div><div>AIR-CON 0.0</div></div><div id="fresh">Cerbo feeds &lt; 5 min</div><script>var keep="Fronius in a script must NOT be touched";</script></body>`;
let fails=0; const check=(n,c)=>{ console.log((c?'  ok ':'  FAIL ')+n); if(!c) fails++; };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
  const dom=new JSDOM(page,{runScripts:'outside-only',pretendToBeVisual:true}); const w=dom.window, d=w.document;
  w.BSF_CONFIG={acSolarName:'ABB ×2',hasAirCon:false,hwConnected:false,sourceName:'system'}; w.state={hw:{hwState:'on'}};
  let calls=0; const RealMO=w.MutationObserver; w.MutationObserver=class extends RealMO{ constructor(cb){ super((...x)=>{calls++; cb(...x);}); } };
  w.eval('var state=window.state;'+block);
  await sleep(50);
  const vis=id=>d.getElementById(id).style.display!=='none';
  check('every visible "Fronius" renamed', !/Fronius|FRONIUS/.test(d.body.textContent.replace(/var keep=.*?;/,'')) && d.getElementById('m_pv_sub').textContent==='DC 0 + ABB ×2 2,646');
  check('text inside <script> left alone', d.querySelector('script').textContent.includes('Fronius in a script'));
  check('air-con box, badge, particle, wire, tap target, card and weekly tile all hidden', !vis('load_ac')&&!vis('d_ac')&&!vis('b_ac')&&!vis('p_ac')&&!vis('c_sur_ac')&&d.querySelector('[data-card="ac"]').style.display==='none'&&d.getElementById('wk_ac').closest('.metric').style.display==='none'&&d.querySelector("rect[onclick*=\"'ac'\"]").style.display==='none');
  check('hot-water tap target NOT hidden', d.querySelector("rect[onclick*=\"'hw'\"]").style.display!=='none');
  check('energy-card air-con tile hidden, others kept', [...d.getElementById('en_cards').children].map(x=>x.style.display).join(',')===',,none');
  check('"Cerbo" wording becomes the site source name', d.getElementById('fresh').textContent==='system feeds < 5 min');
  check('hot water: value is a dash (no collision), badge WOULD RUN, sub-line says not connected', d.getElementById('d_hw').textContent==='—' && d.getElementById('d_hw_today').textContent.startsWith('not connected') && d.getElementById('b_hw').textContent==='WOULD RUN' && !d.getElementById('load_hw').classList.contains('on') && !d.getElementById('p_hw').classList.contains('on'));
  // the page redraws every tick: simulate 20 redraws that put the false claims back
  for(let i=0;i<20;i++){ d.getElementById('d_hw').textContent='≈ 1,100 W'; d.getElementById('b_hw').textContent='ON'; d.getElementById('load_hw').classList.add('on'); d.getElementById('d_totac_src').textContent='Fronius '+(2600+i); await sleep(10); }
  await sleep(80);
  check('after 20 redraws the truth still stands', d.getElementById('d_hw').textContent==='—' && d.getElementById('b_hw').textContent==='WOULD RUN' && !d.getElementById('load_hw').classList.contains('on') && d.getElementById('d_totac_src').textContent==='ABB ×2 2619');
  const before=calls; await sleep(600); const idle=calls-before;
  check('observer SETTLES when the page is idle (no runaway loop): '+idle+' callbacks in 600 ms of quiet', idle<=2);
  check('total observer callbacks stayed proportional to redraws: '+calls, calls<200);
  w.state.hw.hwState='off'; d.getElementById('d_hw').textContent='0 W'; await sleep(60);
  check('when the dispatcher says off it reads WAITING', d.getElementById('b_hw').textContent==='WAITING' && d.getElementById('d_hw_today').textContent==='not connected · no switch yet');
  // a normal farm-like site must be left completely alone
  const dom2=new JSDOM(page,{runScripts:'outside-only'}); dom2.window.BSF_CONFIG={acSolarName:'Fronius',hasAirCon:true,hwConnected:true}; dom2.window.eval(block); await sleep(40);
  check('a site WITH a Fronius, an air-con and a connected plug is untouched', dom2.window.document.getElementById('d_hw').textContent==='≈ 1,100 W' && dom2.window.document.getElementById('load_ac').style.display!=='none' && /Fronius/.test(dom2.window.document.getElementById('m_pv_sub').textContent));
  // a site with NO DC chargers (chargers: []): no DC box, no per-tracker anything, no foregone estimate
  const page3=`<body><svg><g id="mpptBox"><text>DC solar</text></g><path id="c_sun_arr"/><path id="c_arr_bat"/><circle id="p_gen"/><circle id="p_gen2"/>
   <rect class="node-tap" onclick="openDetail('dc')"/><rect class="node-tap" onclick="openDetail('fron')"/><text id="d_hw"></text><text id="b_hw"></text><text id="d_hw_today"></text></svg>
   <div id="drill">DC MPPT — per-charger</div><span id="hwState" class="st on">● ON</span>
   <div class="metric"><span id="m_hw">≈ 0</span><div class="sub2" id="m_hw_sub">plug .37</div></div>
   <div id="m_pv_sub">DC 0 + Fronius 4,700</div><div class="metric"><span id="wk_solar">111</span><div class="sub2">DC + Fronius</div></div>
   <div class="metric"><span id="wk_ess">76</span><div class="sub2">excl. HW + A/C</div></div>
   <div class="curt-fg">~0.0 kWh solar foregone</div><div id="curt_trk"><div class="ctk">MPPT-288</div></div>
   <div><b>Data source:</b> <span id="srcLine">live</span> Read-only with <b>one</b> exception — the AC heating setpoints.</div></body>`;
  const dom3=new JSDOM(page3,{runScripts:'outside-only'}); const w3=dom3.window, d3=w3.document;
  w3.BSF_CONFIG={acSolarName:'ABB ×2',hasAirCon:false,hwConnected:false,sourceName:'system',chargers:[]}; w3.state={hw:{hwState:'on'}};
  w3.eval('var state=window.state; var NODES={sun:{sub:"DC MPPT + Fronius"}};'+block); await sleep(40);
  const hid=id=>d3.getElementById(id).style.display==='none';
  check('no-DC site: DC box, its wires, dots, tap target and drill-down hidden', ['mpptBox','c_sun_arr','c_arr_bat','p_gen','p_gen2','drill'].every(hid) && d3.querySelector("rect[onclick*=\"'dc'\"]").style.display==='none' && d3.querySelector("rect[onclick*=\"'fron'\"]").style.display!=='none');
  check('no-DC site: no MPPT text anywhere visible, curtailment is one whole-site line', !/MPPT/.test(d3.getElementById('curt_trk').textContent) && /Whole-site/.test(d3.getElementById('curt_trk').textContent) && d3.querySelector('.curt-fg').style.display==='none');
  check('no-DC site: solar sub-lines say all AC-coupled (live tile, weekly tile, Total Solar detail)', d3.getElementById('m_pv_sub').textContent==='all AC-coupled · ABB ×2' && d3.getElementById('wk_solar').nextElementSibling.textContent==='all AC-coupled · ABB ×2' && w3.eval('NODES.sun.sub')==='all AC-coupled · ABB ×2');
  for(let i=0;i<10;i++){ d3.getElementById('curt_trk').innerHTML='<div class="ctk">MPPT-288 '+i+'</div>'; d3.getElementById('m_pv_sub').textContent='DC 0 + Fronius 47'+i; await sleep(10); }
  await sleep(40);
  check('no-DC site: per-tracker bars stay gone after 10 redraws', !/MPPT|Fronius/.test(d3.getElementById('curt_trk').textContent+d3.getElementById('m_pv_sub').textContent));
  check('unconnected hot water: card pill and draw tile never claim ON or a plug', d3.getElementById('hwState').textContent==='WOULD RUN' && d3.getElementById('hwState').className==='st arming' && d3.getElementById('m_hw_sub').textContent==='not connected' && d3.getElementById('m_hw').textContent==='—');
  check('no air-con: essentials tile and footer drop the air-con wording', d3.getElementById('wk_ess').nextElementSibling.textContent==='excl. hot water' && !/heating|exception/.test(d3.getElementById('srcLine').parentNode.textContent) && /cannot switch anything/.test(d3.getElementById('srcLine').parentNode.textContent));
  // chargers absent from config (older dashboard-config.js) must NOT be read as "no DC"
  const dom4=new JSDOM(page3,{runScripts:'outside-only'}); dom4.window.BSF_CONFIG={acSolarName:'ABB ×2'}; dom4.window.eval(block); await sleep(30);
  check('config without a chargers list keeps the DC box', dom4.window.document.getElementById('mpptBox').style.display!=='none');
  // layout guards (static: jsdom has no layout). Measured in a real browser on 2026-10-02: "88.8%" and "100%" at 11 px bold are
  // inside the ring's 46 px hole; the title must start right of the ring (ring outer edge x=161).
  const soc=html.match(/<text id="d_soc"[^>]*font-size="([\d.]+)"/);
  check('battery % readable but inside the ring (13-14 px)', soc && +soc[1]>=13 && +soc[1]<=14);
  check('battery % shown as a whole number (a decimal will not fit the ring)', /\$\('d_soc'\)\.textContent=\(soc===null\?'–':Math\.round\(soc\)\+'%'\)/.test(html));
  const bt=html.match(/<text class="node-label" x="(\d+)" y="\d+" text-anchor="middle">Battery bank<\/text>/);
  check('"Battery bank" title centred clear of the ring (x >= 205)', bt && +bt[1]>=205);
  const ly=html.match(/txtCurtY=cb1\+(\d+), lblY=cb1\+(\d+),/);
  check('detail chart: time labels sit >= 10 px below the "curtailed" caption', ly && (+ly[2])-(+ly[1])>=10);
  // Reopen refresh (2 Oct): a page hidden for over a minute, or restored from the back-forward cache, reloads itself
  // so nobody has to pull down to refresh. Run the page's own snippet with a fake clock and a counted reload().
  {
    const m = html.match(/\(function\(\)\{\n  var hiddenAt = document\.hidden[\s\S]*?\}\)\(\);/);
    check('reopen-refresh snippet present', !!m);
    if (m) {
      const run = (hiddenFor, persisted) => {
        const dom2 = new JSDOM('<!doctype html><p>', {runScripts: 'outside-only'}); const w2 = dom2.window;
        let now = 1e12, reloads = 0, hidden = false;
        Object.defineProperty(w2.document, 'hidden', {get: () => hidden});
        w2.Date.now = () => now;
        w2.eval('(function(location){' + m[0] + '})({reload:function(){window.__r=(window.__r||0)+1}});');
        if (persisted != null) { const e = new w2.Event('pageshow'); e.persisted = persisted; w2.dispatchEvent(e); }
        else { hidden = true; w2.document.dispatchEvent(new w2.Event('visibilitychange'));
               now += hiddenFor; hidden = false; w2.document.dispatchEvent(new w2.Event('visibilitychange')); }
        return w2.__r || 0;
      };
      check('hidden 2 min then shown -> reloads', run(120000) === 1);
      check('hidden 10 s then shown -> no reload (quick app switch)', run(10000) === 0);
      check('restored from back-forward cache -> reloads', run(null, true) === 1);
      check('normal first load (not from cache) -> no reload', run(null, false) === 0);
    }
  }
  console.log('\n'+fails+' failure(s)'); process.exit(fails?1:0);
})();

