// A poll must never take text out from under a person selecting it (#190).
export function selecting(node){
 const selection=globalThis.getSelection?.();
 if(!selection||selection.isCollapsed||!selection.rangeCount)return false;
 return node.contains(selection.anchorNode)||node.contains(selection.focusNode);
}
// Redraw only when the data changed and nobody is selecting inside the node; the next poll catches up.
export function redrawIfChanged(node,signature,draw){
 if(node.dataset.signature===signature||selecting(node))return false;
 node.dataset.signature=signature;draw();return true;
}

export function mergeAskState(base,next,limit=500){
 if(!base)return next;
 if(next.not_modified)return {...base,...next,messages:base.messages};
 // A full snapshot is the server resyncing: it is authoritative even at a LOWER
 // revision (Ask DB recreated, or the phone reached a different door whose
 // counter never ran as high). Refusing it froze the cached conversation
 // forever: sends were accepted server-side and never rendered. Only a stale
 // DELTA is refused below, because a delta is a patch on a base it must match.
 if(!next.delta)return next;
 if(Number(next.revision)<Number(base.revision))return base;
 const rows=new Map(base.messages.map(row=>[row.id,row]));
 for(const row of next.messages)rows.set(row.id,row);
 return {...base,...next,messages:[...rows.values()].sort((a,b)=>a.id-b.id).slice(-limit),not_modified:false};
}
