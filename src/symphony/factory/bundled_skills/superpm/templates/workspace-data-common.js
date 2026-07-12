/* templates/workspace-data-common.js — 모듈 분리 모드 SSOT 루트 + 등록 계약
   ============================================================================
   대규모(다수 모듈/화면) 스토리보드에서 workspace.html을 도메인(모듈)별로 분리할 때 사용.
   배치: <project>/data/_common.js 로 복사하고, 모듈마다 data/NN-<module>.js 를 둔다.
   로드: workspace.html 하단에서 app 로직보다 먼저 <script src>로 읽는다.
     <script src="data/_common.js"></script>
     <script src="data/01-<module>.js"></script>   ... (모듈 순서대로)
     <script>  // app 로직 (window.SSOT 를 읽음)
   file:// 더블클릭으로 열어도 동작한다(<script src>는 CORS 비대상).
   단일 모듈/소규모면 이 파일 없이 workspace.html 인라인 #ssot 하나로 충분하다.
   ----------------------------------------------------------------------------
   registerModule({ id, label, order, features:[], specs:[], screens:[], flow:{nodes:[],edges:[]} })
   - id        모듈(섹션) id (기능명세 파일과 1:1 권장)
   - order     표시 순서(작을수록 먼저)
   - screens[] {id,title,nav,device,state,prev,next,flowNodeId,mode,elements:[{calloutNo,area,selector,behavior,dataContract,exception,linkedSpecId}],preview}
   - flow      {nodes:[{id,type,label,linkedSpecIds,entry?,entryLabel?,hub?}],edges:[{from,to,condition}]}
   - 진입 노드 1개에 entry:true (홈/허브 노드 hub:true → 허브→엔트리 자동 엣지)
   식별자/규칙: superpm reference/model.md (R2/R3/R4, ORPHAN_NODE/UNMAPPED_SCREEN/...).
   ========================================================================== */
window.SSOT = {
  brand: "{제품명}",
  navUser: "{사용자} · 로그아웃",
  snapshotId: "{snapshot-id}",
  nav: [
    // 전역 메뉴(LNB). target 없는 항목은 비클릭(범위 밖).
    // {id:"home", label:"홈", icon:"⌂", target:"{진입 screen_id}"},
  ],
  modules:  [],
  features: [],
  specs:    [],
  screens:  [],
  flow:     { nodes: [], edges: [] }
};

window.registerModule = function(m){
  if(!m || !m.id){ return; }
  window.SSOT.modules.push({ id:m.id, label:m.label||m.id, order:(m.order!=null?m.order:99) });
  (m.features||[]).forEach(function(f){ f.moduleId=m.id; window.SSOT.features.push(f); });
  (m.specs||[]).forEach(function(s){ s.moduleId=m.id; window.SSOT.specs.push(s); });
  (m.screens||[]).forEach(function(s){ s.moduleId=m.id; window.SSOT.screens.push(s); });
  if(m.flow){
    (m.flow.nodes||[]).forEach(function(n){ n.moduleId=m.id; window.SSOT.flow.nodes.push(n); });
    (m.flow.edges||[]).forEach(function(e){ window.SSOT.flow.edges.push(e); });
  }
};
