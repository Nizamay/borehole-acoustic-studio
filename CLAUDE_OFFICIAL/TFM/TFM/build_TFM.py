#!/usr/bin/env python3
"""
Temperature Field Map v21 - Build Script
Applies all v21 patches to Temperature_Field_Map_v20.html

Usage:
    python build_TFM_v21.py <input_v20.html> <output_v21.html>

Example:
    python build_TFM_v21.py Temperature_Field_Map_v20.html Temperature_Field_Map_v21.html
"""

import sys
import re


def apply_patch(content, old, new, name):
    if old in content:
        content = content.replace(old, new, 1)
        print(f"  ✅ {name}")
        return content
    else:
        print(f"  ❌ NOT FOUND: {name}")
        return content


def build(input_path, output_path):
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    print("Applying patches...\n")

    # ── 1. resolveWellKey — robust numeric fuzzy match ────────────────
    content = apply_patch(content,
        """function resolveWellKey(wb){
  if(S.wells[wb]) return wb;
  // Try NIMR-XXX → NR-XXX and vice versa
  const alt1=wb.replace(/^NIMR-/,'NR-').replace(/^NR-/,'NIMR-');
  if(S.wells[alt1]) return alt1;
  // Numeric suffix match
  const m=wb.match(/(\\d+[A-Z0-9]*)$/i);
  if(m){const s=m[1].toUpperCase();const found=Object.keys(S.wells).find(k=>k.toUpperCase().endsWith(s));if(found)return found;}
  return wb; // return as-is (will be unresolved)
}""",
        """function resolveWellKey(wb){
  if(!wb)return wb;
  if(S.wells[wb])return wb;
  if(S.coords[wb])return wb;
  const num=s=>(s.match(/\\d{3,}/g)||[]).join('');
  const norm=s=>s.toUpperCase().replace(/^(NIMR|NM|NR)[-_\\s]*/,'').replace(/[-_\\s]+/g,'');
  const wbNorm=norm(wb),wbNum=num(wb);
  for(const k of Object.keys(S.wells)){
    if(norm(k)===wbNorm)return k;
    if(wbNum&&num(k)===wbNum)return k;
  }
  for(const k of Object.keys(S.coords)){
    if(norm(k)===wbNorm)return k;
    if(wbNum&&num(k)===wbNum)return k;
  }
  return wb;
}""",
        "resolveWellKey numeric fuzzy match"
    )

    # ── 2. rXY — fuzzy coord lookup ───────────────────────────────────
    content = apply_patch(content,
        """function rXY(wk){
  if(S.coords[wk])return S.coords[wk];
  const a=wk.replace(/^NIMR-/,'NM-').replace(/^NR-/,'NM-');
  if(S.coords[a])return S.coords[a];
  // Fuzzy match: normalize separators and case
  const norm=s=>s.toUpperCase().replace(/[\\s\\-_]+/g,'');
  const wkn=norm(wk);
  for(const ck of Object.keys(S.coords)){if(norm(ck)===wkn)return S.coords[ck];}
  const m=wk.match(/(\\d+[A-Z0-9]*)$/i);
  if(m){const s=m[1].toUpperCase();for(const k of Object.keys(S.coords))if(k.toUpperCase().endsWith(s))return S.coords[k];}
  return null;
}""",
        """function rXY(wk){
  const num=s=>(s.match(/\\d{3,}/g)||[]).join('');
  const norm=s=>s.toUpperCase().replace(/^(NIMR|NM|NR)[-_\\s]*/,'').replace(/[-_\\s]+/g,'');
  if(S.coords[wk])return S.coords[wk];
  const wkn=norm(wk),wkNum=num(wk);
  for(const ck of Object.keys(S.coords)){if(norm(ck)===wkn)return S.coords[ck];}
  if(wkNum){for(const ck of Object.keys(S.coords)){if(num(ck)===wkNum)return S.coords[ck];}}
  return null;
}""",
        "rXY numeric fuzzy match"
    )

    # ── 3. resolveDevKey + devXY ──────────────────────────────────────
    content = apply_patch(content,
        "function devConv(wk){",
        """function resolveDevKey(wk){
  if(S.dev[wk])return wk;
  const num=s=>(s.match(/\\d{3,}/g)||[]).join('');
  const norm=s=>s.toUpperCase().replace(/^(NIMR|NM|NR)[-_\\s]*/,'').replace(/[-_\\s]+/g,'');
  const wkn=norm(wk),wkNum=num(wk);
  for(const dk of Object.keys(S.dev)){
    if(norm(dk)===wkn)return dk;
    if(wkNum&&num(dk)===wkNum)return dk;
  }
  return wk;
}
function devXY(wk,md){
  const dev=S.dev?.[resolveDevKey(wk)];
  if(!dev||dev.length<2)return{dx:0,dy:0};
  const ks=Object.keys(dev[0]);
  const fk=(...p)=>ks.find(k=>p.some(x=>x instanceof RegExp?x.test(k):k===x))||null;
  const mdK=fk('md',/mahdbf|mahtbf/i,/\\bmd\\b/i)||ks[2];
  const dxK=fk('dispx',/disp\\s*x/i,/\\bns\\b/i,/northing/i);
  const dyK=fk('dispy',/disp\\s*y/i,/\\bew\\b/i,/easting/i);
  if(!dxK||!dyK||!mdK)return{dx:0,dy:0};
  const pts=dev.filter(r=>r[mdK]!=null&&r[dxK]!=null)
    .map(r=>({md:+r[mdK]||0,dx:+r[dxK]||0,dy:+r[dyK]||0}))
    .sort((a,b)=>a.md-b.md);
  if(!pts.length)return{dx:0,dy:0};
  if(md<=pts[0].md)return{dx:pts[0].dx,dy:pts[0].dy};
  const last=pts[pts.length-1];
  if(md>=last.md)return{dx:last.dx,dy:last.dy};
  let lo=0,hi=pts.length-1;
  while(hi-lo>1){const m=(lo+hi)>>1;if(pts[m].md<=md)lo=m;else hi=m;}
  const t=(md-pts[lo].md)/(pts[hi].md-pts[lo].md);
  return{dx:pts[lo].dx+t*(pts[hi].dx-pts[lo].dx),dy:pts[lo].dy+t*(pts[hi].dy-pts[lo].dy)};
}
function devConv(wk){""",
        "resolveDevKey + devXY"
    )

    # ── 4. devConv uses resolveDevKey ─────────────────────────────────
    content = apply_patch(content,
        "function devConv(wk){\n  const dev=S.dev[wk];",
        "function devConv(wk){\n  const dev=S.dev[resolveDevKey(wk)];",
        "devConv uses resolveDevKey"
    )

    # ── 5. handleDev normalize fields ─────────────────────────────────
    content = apply_patch(content,
        "      S.dev[wb]=rows.map(r=>{const o={};hdr.forEach((h,i)=>{if(h&&r[i]!=null)o[h]=r[i];});return o;});n++;",
        """      S.dev[wb]=rows.map(r=>{
        const o={};hdr.forEach((h,i)=>{if(h&&r[i]!=null)o[h]=r[i];});
        if(o.md==null)   o.md   =o['AHD (mahdbf)']??o['AHD (mahtbf)']??o['MD']??null;
        if(o.dispx==null)o.dispx=o['Disp X (m)']??o['NS (m)']??null;
        if(o.dispy==null)o.dispy=o['Disp Y (m)']??o['EW (m)']??null;
        if(o.inc==null)  o.inc  =o['Inclination (deg)']??null;
        if(o.az==null)   o.az   =o['Azimuth (deg)']??null;
        return o;
      });n++;""",
        "handleDev normalize fields"
    )

    # ── 6. handleDev triggers replot ──────────────────────────────────
    content = apply_patch(content,
        "  toast(`${n} deviation file(s)`);$('btn-dev').classList.add('ok');setLoading(false);\n}",
        "  toast(`${n} deviation file(s)`);$('btn-dev').classList.add('ok');setLoading(false);\n  if(n>0)replotAll();\n}",
        "handleDev triggers replot"
    )

    # ── 7. loadProject normalize dev ──────────────────────────────────
    content = apply_patch(content,
        "    S.dev=p.dev||{};",
        """    S.dev={};
    Object.entries(p.dev||{}).forEach(([wk,rows])=>{
      S.dev[wk]=rows.map(r=>{
        const o=Object.assign({},r);
        if(o.md==null)   o.md   =o['AHD (mahdbf)']??o['AHD (mahtbf)']??o['MD']??null;
        if(o.dispx==null)o.dispx=o['Disp X (m)']??o['NS (m)']??null;
        if(o.dispy==null)o.dispy=o['Disp Y (m)']??o['EW (m)']??null;
        if(o.inc==null)  o.inc  =o['Inclination (deg)']??null;
        if(o.az==null)   o.az   =o['Azimuth (deg)']??null;
        return o;
      });
    });""",
        "loadProject normalize dev"
    )

    # ── 8. loadProject wellInfo fuzzy re-key ──────────────────────────
    content = apply_patch(content,
        "    S.wellInfo=p.wellInfo||{};",
        """    S.wellInfo={};
    Object.entries(p.wellInfo||{}).forEach(([k,v])=>{
      if(!k||k.length<4||!/\\d/.test(k))return;
      const resolvedKey=resolveWellKey(k);
      S.wellInfo[resolvedKey]=v;
    });""",
        "loadProject wellInfo fuzzy re-key"
    )

    # ── 9. render3D grey deviation track + colored LAS track ──────────
    content = apply_patch(content,
        """    const wd=S.wells[k];const xy=rXY(k);if(!xy)return;
    const arr=cArr(wd,colorBy)||[];
    const xs=[],ys=[],zs=[],cs2=[],txts=[];
    wd.dept.forEach((md,i)=>{
      if(md==null)return;
      xs.push(xy.lon);ys.push(xy.lat);zs.push(-cD(md,k,dm));""",
        """    const wd=S.wells[k];const xy=rXY(k);if(!xy)return;
    const arr=cArr(wd,colorBy)||[];
    const xs=[],ys=[],zs=[],cs2=[],txts=[];
    // Grey deviation track (full wellbore path)
    const _dp=S.dev?.[resolveDevKey(k)];
    const _gx=[],_gy=[],_gz=[];
    const _lasMaxD=Math.max(...(wd.dept.filter(v=>v!=null)));
    if(_dp&&_dp.length>1){
      [..._dp].filter(r=>(r.md??r['AHD (mahdbf)'])!=null)
        .sort((a,b)=>(+(a.md??a['AHD (mahdbf)']))-(+(b.md??b['AHD (mahdbf)'])))
        .forEach(r=>{
          const _m=+(r.md??r['AHD (mahdbf)']);
          if(_m>_lasMaxD*1.5)return;
          _gx.push(xy.lon+(+(r.dispx??r['Disp X (m)']??0)));
          _gy.push(xy.lat+(+(r.dispy??r['Disp Y (m)']??0)));
          _gz.push(-_m);
        });
    } else {
      const _d=wd.dept.filter(v=>v!=null);
      if(_d.length){_gx.push(xy.lon,xy.lon);_gy.push(xy.lat,xy.lat);_gz.push(-Math.min(..._d),-Math.max(..._d));}
    }
    if(_gx.length>1)traces.push({type:'scatter3d',mode:'lines',
      x:_gx,y:_gy,z:_gz,
      line:{color:'rgba(100,130,160,0.35)',width:2},
      hoverinfo:'skip',showlegend:false,name:'__dev__',
      projection:{x:{show:false},y:{show:false},z:{show:false}}});
    wd.dept.forEach((md,i)=>{
      if(md==null)return;
      const {dx,dy}=devXY(k,md);
      xs.push(xy.lon+dx);ys.push(xy.lat+dy);zs.push(-cD(md,k,dm));""",
        "render3D grey+colored track"
    )

    # ── 10. render3D disable wall projections ─────────────────────────
    content = apply_patch(content,
        "      text:txts,hoverinfo:'text',name:wd.well,showlegend:true});",
        "      text:txts,hoverinfo:'text',name:wd.well,showlegend:true,\n      projection:{x:{show:false},y:{show:false},z:{show:false}}});",
        "render3D disable wall projections"
    )

    # ── 11. render3D well label at surface ────────────────────────────
    content = apply_patch(content,
        "  traces.push({type:'scatter3d',mode:'text',x:[xy.lon],y:[xy.lat],z:[-(wd.strt||0)],",
        "  traces.push({type:'scatter3d',mode:'text',x:[xy.lon],y:[xy.lat],z:[0],",
        "render3D label at surface"
    )

    # ── 12. render3D hide axis backgrounds ────────────────────────────
    content = apply_patch(content,
        "xaxis:{title:isUTM()?'Easting (m)':'Lon',color:'#5a7599',gridcolor:'#1f2d45',tickfont:{family:'IBM Plex Mono',size:7}},",
        "xaxis:{title:isUTM()?'Easting (m)':'Lon',color:'#5a7599',gridcolor:'#1f2d45',tickfont:{family:'IBM Plex Mono',size:7},showbackground:false,showspikes:false},",
        "render3D xaxis showbackground:false"
    )
    content = apply_patch(content,
        "yaxis:{title:isUTM()?'Northing (m)':'Lat',color:'#5a7599',gridcolor:'#1f2d45',tickfont:{family:'IBM Plex Mono',size:7}},",
        "yaxis:{title:isUTM()?'Northing (m)':'Lat',color:'#5a7599',gridcolor:'#1f2d45',tickfont:{family:'IBM Plex Mono',size:7},showbackground:false,showspikes:false},",
        "render3D yaxis showbackground:false"
    )

    # ── 13. render3D Z axis range from LAS depth ──────────────────────
    content = apply_patch(content,
        "zaxis:{title:`Depth (${dm})`,color:'#5a7599',gridcolor:'#1f2d45',tickfont:{family:'IBM Plex Mono',size:7}},",
        "zaxis:{title:`Depth (${dm})`,color:'#5a7599',gridcolor:'#1f2d45',tickfont:{family:'IBM Plex Mono',size:7},range:(()=>{const d=Object.values(S.wells).flatMap(w=>w.dept.filter(v=>v!=null));return d.length?[-Math.max(...d)*1.1,Math.max(...d)*0.05]:null;})()},",
        "render3D zaxis range from LAS depth"
    )

    # ── 14. render3D padding markers for scene centering ─────────────
    content = apply_patch(content,
        "  // Wells\n  wks.forEach((k,ki)=>{",
        """  // Invisible padding to center scene on wells
  wks.forEach(k=>{
    const xy=rXY(k);if(!xy)return;
    const pad=50;
    traces.push({type:'scatter3d',mode:'markers',
      x:[xy.lon-pad,xy.lon+pad],y:[xy.lat-pad,xy.lat+pad],z:[0,0],
      marker:{size:0.01,opacity:0,color:'rgba(0,0,0,0)'},
      hoverinfo:'skip',showlegend:false,name:'__pad__'});
  });

  // Wells
  wks.forEach((k,ki)=>{""",
        "render3D padding markers"
    )

    # ── 15. render3D hide Plotly legend ───────────────────────────────
    content = apply_patch(content,
        "    legend:{bgcolor:'rgba(22,29,46,.8)',bordercolor:'#1f2d45',borderwidth:1,font:{family:'IBM Plex Mono',size:7,color:'#d8e4f0'},x:.01,y:.1},",
        "    showlegend:false,",
        "render3D hide Plotly legend"
    )

    # ── 16. Formation Tops — wider dropdown ───────────────────────────
    content = apply_patch(content,
        """  <div class="ss">
    <div class="sst" onclick="tss(this)">Formation Tops <span class="cnt" id="fmcnt">0</span><span class="arr">▾</span></div>
    <div class="ssb">
      <div class="ctrl">
        <label>Edit for well</label>
        <select id="fme-well" onchange="refreshFmEd()"><option value="">— Select well —</option></select>
      </div>
      <div class="fme-rows" id="fme-rows"></div>
      <div class="fme-add">
        <input type="text" id="fme-name" placeholder="Formation" style="font-size:9px">
        <input type="number" id="fme-md" placeholder="MD(m)" style="font-size:9px">
        <button class="btn btn-a btn-sm" onclick="addTopW()">＋</button>
      </div>
    </div>
  </div>""",
        """  <div class="ss">
    <div class="sst" onclick="tss(this)">Formation Tops <span class="cnt" id="fmcnt">0</span><span class="arr">▾</span></div>
    <div class="ssb">
      <select id="fme-well" style="width:100%;font-family:var(--mono);font-size:8px;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:2px;padding:2px;margin-bottom:4px" onchange="refreshFmEd()">
        <option value="">— Select well —</option>
      </select>
      <div class="fme-rows" id="fme-rows"></div>
      <div class="fme-add" style="margin-top:4px">
        <input type="text" id="fme-name" placeholder="Formation" style="font-size:9px">
        <input type="number" id="fme-md" placeholder="MD(m)" style="font-size:9px">
        <button class="btn btn-a btn-sm" onclick="addTopW()">＋</button>
      </div>
    </div>
  </div>""",
        "Formation Tops wider dropdown"
    )

    # ── 17. Manual Coordinates → Well Data ───────────────────────────
    content = apply_patch(content,
        """  <div class="ss">
    <div class="sst" onclick="tss(this)">Manual Coordinates<span class="arr">▾</span></div>
    <div class="ssb">
      <div class="mc-rows" id="mc-rows"></div>
      <div class="mc-add" style="display:flex;flex-direction:column;gap:3px">
        <div style="display:flex;gap:3px">
          <input type="text" id="mc-w" placeholder="Well name" style="font-size:9px;flex:2">
          <input type="number" id="mc-lon" placeholder="Lon" step=".0001" style="font-size:9px;flex:1">
          <input type="number" id="mc-lat" placeholder="Lat" step=".0001" style="font-size:9px;flex:1">
        </div>
        <div style="display:flex;gap:3px">
          <select id="mc-type-new" style="font-family:var(--mono);font-size:8px;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:2px;flex:1;padding:1px">
            <option value="">— Type —</option>
            <option value="Producer">Producer</option>
            <option value="Injector">Injector</option>
            <option value="Observer">Observer</option>
          </select>
          <select id="mc-fluid-new" style="font-family:var(--mono);font-size:8px;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:2px;flex:1;padding:1px">
            <option value="">— Fluid —</option>
            <option value="oil">Oil</option>
            <option value="water">Water</option>
            <option value="gas">Gas</option>
          </select>
          <button class="btn btn-a btn-sm" onclick="addMC()" style="flex-shrink:0">＋</button>
        </div>
      </div>
    </div>
  </div>""",
        """  <div class="ss">
    <div class="sst" onclick="tss(this)">Well Data<span class="arr">▾</span></div>
    <div class="ssb">
      <div style="margin-bottom:4px">
        <select id="wd-well-sel" style="width:100%;font-family:var(--mono);font-size:8px;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:2px;padding:2px" onchange="refreshWDEditor()">
          <option value="">— Select well —</option>
        </select>
      </div>
      <div id="wd-editor" style="display:flex;flex-direction:column;gap:3px;margin-bottom:4px"></div>
      <div style="font-family:var(--mono);font-size:8px;color:var(--sub);margin-bottom:3px;letter-spacing:1px">ADD WELL</div>
      <div style="display:flex;flex-direction:column;gap:3px">
        <input type="text" id="mc-w" placeholder="Well name" style="font-size:9px">
        <div style="display:flex;gap:3px">
          <input type="number" id="mc-lon" placeholder="Easting" step=".01" style="font-size:9px;flex:1">
          <input type="number" id="mc-lat" placeholder="Northing" step=".01" style="font-size:9px;flex:1">
        </div>
        <div style="display:flex;gap:3px">
          <select id="mc-type-new" style="font-family:var(--mono);font-size:8px;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:2px;flex:1;padding:1px">
            <option value="">— Type —</option>
            <option value="Producer">Producer</option>
            <option value="Injector">Injector</option>
            <option value="Observer">Observer</option>
          </select>
          <select id="mc-fluid-new" style="font-family:var(--mono);font-size:8px;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:2px;flex:1;padding:1px">
            <option value="">— Fluid —</option>
            <option value="oil">Oil</option>
            <option value="water">Water</option>
            <option value="gas">Gas</option>
          </select>
        </div>
        <button class="btn btn-a btn-sm" onclick="addMC()" style="width:100%">＋ Add well</button>
      </div>
    </div>
  </div>""",
        "Manual Coordinates → Well Data with dropdown"
    )

    # ── 18. saveWI uses resolveWellKey ────────────────────────────────
    content = apply_patch(content,
        """function saveWI(k){
  if(!S.wellInfo)S.wellInfo={};
  if(!S.wellInfo[k])S.wellInfo[k]={};
  const ts=document.getElementById('mc-type-'+k);
  const fs=document.getElementById('mc-fluid-'+k);
  if(ts)S.wellInfo[k].type=ts.value;
  if(fs)S.wellInfo[k].fluid=fs.value;
  render3D();renderMinimap();renderMap();
}""",
        """function saveWI(k){
  if(!S.wellInfo)S.wellInfo={};
  const rk=resolveWellKey(k)||k;
  if(!S.wellInfo[rk])S.wellInfo[rk]={};
  const ts=document.getElementById('mc-type-'+k);
  const fs=document.getElementById('mc-fluid-'+k);
  if(ts)S.wellInfo[rk].type=ts.value;
  if(fs)S.wellInfo[rk].fluid=fs.value;
  render3D();renderMinimap();renderMap();
}""",
        "saveWI uses resolveWellKey"
    )

    # ── 19. refreshMCRows → refreshWDEditor ───────────────────────────
    content = apply_patch(content,
        """function refreshMCRows(){
  const box=$('mc-rows');box.innerHTML='';
  Object.entries(S.coords).forEach(([n,c])=>{
    const r=document.createElement('div');r.className='mc-row';
    // Fuzzy match wellInfo key
    const _wiKey=(()=>{
      if(S.wellInfo?.[n])return n;
      const num=s=>s.match(/\\d{3,}/g)?.join('')||null;
      const norm=s=>s.toUpperCase().replace(/^(NIMR|NM|NR)[-_\\s]*/,'').replace(/[-_\\s]+/g,'');
      const nn=norm(n),nNum=num(n);
      for(const wk of Object.keys(S.wellInfo||{})){
        if(norm(wk)===nn)return wk;
        if(nNum&&num(wk)===nNum)return wk;
      }
      return n;
    })();
    const wi=S.wellInfo?.[_wiKey]||{};
    r.innerHTML=`<span style="font-family:var(--mono);font-size:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${n}">${n}</span>
      <span style="font-family:var(--mono);font-size:8px;color:var(--sub)">${(+c.lon).toFixed(4)}</span>
      <span style="font-family:var(--mono);font-size:8px;color:var(--sub)">${(+c.lat).toFixed(4)}</span>
      <div class="mc-del" onclick="delMC('${n}')">✕</div>
      <select id="mc-type-${n}" style="font-family:var(--mono);font-size:7px;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:2px" onchange="saveWI('${n}')">
        <option value="">type</option>
        <option value="Producer" ${(wi.type||'')==='Producer'?'selected':''}>Prod</option>
        <option value="Injector" ${(wi.type||'')==='Injector'?'selected':''}>Inj</option>
        <option value="Observer" ${(wi.type||'')==='Observer'?'selected':''}>Obs</option>
      </select>
      <select id="mc-fluid-${n}" style="font-family:var(--mono);font-size:7px;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:2px" onchange="saveWI('${n}')">
        <option value="">fluid</option>
        <option value="oil" ${(wi.fluid||'')==='oil'?'selected':''}>Oil</option>
        <option value="water" ${(wi.fluid||'')==='water'?'selected':''}>Water</option>
        <option value="gas" ${(wi.fluid||'')==='gas'?'selected':''}>Gas</option>
      </select>`;
      // type/fluid selects injected by saveWI
    box.appendChild(r);
  });
}""",
        """function _wiForCoord(n){
  if(S.wellInfo?.[n])return{key:n,wi:S.wellInfo[n]};
  const rk=resolveWellKey(n);
  if(S.wellInfo?.[rk])return{key:rk,wi:S.wellInfo[rk]};
  return{key:n,wi:{}};
}
function refreshMCRows(){
  const sel=$('wd-well-sel');
  if(sel){
    const prev=sel.value;
    sel.innerHTML='<option value="">— Select well —</option>';
    Object.keys(S.coords).forEach(n=>{
      const opt=document.createElement('option');
      opt.value=n;opt.textContent=n;
      if(n===prev)opt.selected=true;
      sel.appendChild(opt);
    });
  }
  refreshWDEditor();
}
function refreshWDEditor(){
  const sel=$('wd-well-sel');
  const box=$('wd-editor');
  if(!box)return;
  box.innerHTML='';
  const n=sel?.value;
  if(!n||!S.coords[n])return;
  const c=S.coords[n];
  const {key,wi}=_wiForCoord(n);
  box.innerHTML=`
    <div style="font-family:var(--mono);font-size:8px;color:var(--sub);display:flex;justify-content:space-between;align-items:center">
      <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1" title="${n}">${n}</span>
      <button onclick="delMC('${n}')" style="background:none;border:none;color:var(--sub);cursor:pointer;font-size:10px;padding:0 2px">✕</button>
    </div>
    <div style="display:flex;gap:3px">
      <div style="flex:1">
        <div style="font-family:var(--mono);font-size:7px;color:var(--sub)">Easting</div>
        <input type="number" id="wd-lon" value="${(+c.lon).toFixed(2)}" step="0.01"
          style="font-size:9px;width:100%" onchange="saveWDCoord('${n}')">
      </div>
      <div style="flex:1">
        <div style="font-family:var(--mono);font-size:7px;color:var(--sub)">Northing</div>
        <input type="number" id="wd-lat" value="${(+c.lat).toFixed(2)}" step="0.01"
          style="font-size:9px;width:100%" onchange="saveWDCoord('${n}')">
      </div>
    </div>
    <div style="display:flex;gap:3px;margin-top:2px">
      <select id="mc-type-${n}" style="font-family:var(--mono);font-size:8px;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:2px;flex:1;padding:2px" onchange="saveWI('${n}')">
        <option value="">— Type —</option>
        <option value="Producer" ${(wi.type||'')==='Producer'?'selected':''}>Producer</option>
        <option value="Injector" ${(wi.type||'')==='Injector'?'selected':''}>Injector</option>
        <option value="Observer" ${(wi.type||'')==='Observer'?'selected':''}>Observer</option>
      </select>
    </div>
    <div style="display:flex;gap:3px;margin-top:2px">
      <select id="mc-fluid-${n}" style="font-family:var(--mono);font-size:8px;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:2px;flex:1;padding:2px" onchange="saveWI('${n}')">
        <option value="">— Fluid —</option>
        <option value="oil" ${(wi.fluid||'')==='oil'?'selected':''}>Oil</option>
        <option value="water" ${(wi.fluid||'')==='water'?'selected':''}>Water</option>
        <option value="gas" ${(wi.fluid||'')==='gas'?'selected':''}>Gas</option>
      </select>
    </div>`;
}
function saveWDCoord(n){
  const lon=parseFloat($('wd-lon')?.value);
  const lat=parseFloat($('wd-lat')?.value);
  if(!isNaN(lon)&&!isNaN(lat)){S.coords[n]={lon,lat};S.mapExtent=null;replotAll();}
}""",
        "refreshMCRows → refreshWDEditor"
    )

    # ── 20. refreshUI populate wd-well-sel ────────────────────────────
    content = apply_patch(content,
        "  ['sel-lw','fme-well'].forEach(id=>{const el=$(id);if(el){el.innerHTML='<option value=\"\">— Well —</option>';}});",
        "  ['sel-lw','fme-well'].forEach(id=>{const el=$(id);if(el){el.innerHTML='<option value=\"\">— Well —</option>';}});\n  if($('wd-well-sel')){$('wd-well-sel').innerHTML='<option value=\"\">— Select well —</option>';}",
        "refreshUI reset wd-well-sel"
    )

    content = apply_patch(content,
        "    ['sel-lw','fme-well'].forEach(id=>{const el=$(id);if(!el)return;const o=document.createElement('option');o.value=k;o.textContent=wd.well;el.appendChild(o);});",
        """    ['sel-lw','fme-well'].forEach(id=>{const el=$(id);if(!el)return;const o=document.createElement('option');o.value=k;o.textContent=wd.well;el.appendChild(o);});
    if($('wd-well-sel')&&rXY(k)){const o=document.createElement('option');o.value=k;o.textContent=wd.well;$('wd-well-sel').appendChild(o);}""",
        "refreshUI populate wd-well-sel from wells"
    )

    content = apply_patch(content,
        "  refreshXsWellList();",
        """  Object.keys(S.coords).forEach(n=>{
    const sel=$('wd-well-sel');if(!sel)return;
    if([...sel.options].some(o=>o.value===n))return;
    const o=document.createElement('option');o.value=n;o.textContent=n;sel.appendChild(o);
  });
  refreshXsWellList();""",
        "refreshUI populate wd-well-sel from coords"
    )

    # ── 21. Formation Tops fmi fix ────────────────────────────────────
    content = apply_patch(content,
        "    const fmi=hdr.findIndex(h=>/formation|fm|name/i.test(h)&&!/well/i.test(h));",
        "    const fmi=(()=>{let i=hdr.findIndex(h=>/formation/i.test(h));if(i>=0)return i;i=hdr.findIndex(h=>/\\bfm\\b/i.test(h));if(i>=0)return i;return hdr.findIndex(h=>/name/i.test(h)&&!/well/i.test(h)&&!/^name$/i.test(h));})();",
        "Formation Tops fmi prioritize 'formation' keyword"
    )

    # ── 22. Formation Tops fuzzy rXY ──────────────────────────────────
    content = apply_patch(content,
        "        const xy=rXY(wb);if(!xy||d.md==null)return;",
        "        const xy=rXY(wb)||rXY(resolveWellKey(wb));if(!xy||d.md==null)return;",
        "Formation Tops fuzzy rXY"
    )

    # ── 23. CSR stream detection ──────────────────────────────────────
    content = apply_patch(content,
        """    const objRe=(\\d+)\\s+0\\s+obj[\\s\\S]*?\\/Length\\s+(\\d+)[\\s\\S]*?stream[\\r\\n]/g;""",
        "SKIP",
        "CSR stream detection (manual patch needed)"
    )

    # ── 24. CSR fluid/type from PDF tokens ────────────────────────────
    content = apply_patch(content,
        "    const hdrM=allText.match(/W\\s*ell\\s+F(?:luid|unction)",
        "SKIP",
        "CSR fluid/type (manual patch needed)"
    )

    # ── 25. Update title ──────────────────────────────────────────────
    content = content.replace(
        '<title>Temperature Field Map v4</title>',
        '<title>Temperature Field Map v21</title>'
    )
    print("  ✅ title updated to v21")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"\n✅ Saved: {output_path}")
    print("\nNote: Patches 23-24 (CSR parser) require manual application.")
    print("Use Temperature_Field_Map_v21_base.html as the definitive base instead.")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    build(sys.argv[1], sys.argv[2])
