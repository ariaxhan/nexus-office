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
 if(base&&Number(next.revision)<Number(base.revision))return base;
 if(!base||!next.delta)return next.not_modified&&base?{...base,...next,messages:base.messages}:next;
 const rows=new Map(base.messages.map(row=>[row.id,row]));
 for(const row of next.messages)rows.set(row.id,row);
 return {...base,...next,messages:[...rows.values()].sort((a,b)=>a.id-b.id).slice(-limit),not_modified:false};
}
