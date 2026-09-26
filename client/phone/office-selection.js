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
