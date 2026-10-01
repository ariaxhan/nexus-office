// Coordinator questions already carried by the current open-issue snapshot.
export function coordinatorQuestions(world){
 return (world.stations||[]).flatMap(station=>(station.issues||[])
  .filter(issue=>issue.bot_last===true&&issue.decision&&/<!-- aria-question:[a-f0-9]+ -->/.test(issue.last_word||''))
  .map(issue=>({kind:'coordinator-question',repo:station.repo,item:{...issue,id:`${station.repo}#${issue.number}`}})));
}
