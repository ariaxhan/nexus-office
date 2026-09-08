export function nativeCommand(data){window.webkit?.messageHandlers?.officeNative?.postMessage(data);}
export function audioTransport(fallback){return window.officeNativeAvailable?new NativeAudio():fallback;}
class NativeAudio extends EventTarget {
 constructor(){super();this.isNative=true;this.state={position:0,duration:0,paused:true,rate:1};this.metadata={};
  window.addEventListener('office-native-audio',event=>this.receive(event.detail));
 }
 configure(episode){this.metadata={title:episode.title,id:episode.id};}
 set src(url){this.state={...this.state,position:0,duration:0,paused:true};nativeCommand({command:'load',url:new URL(url,location.origin).href,...this.metadata});}
 get currentTime(){return this.state.position;}
 set currentTime(position){nativeCommand({command:'seek',position});}
 get duration(){return this.state.duration;}
 get paused(){return this.state.paused;}
 get playbackRate(){return this.state.rate;}
 set playbackRate(rate){nativeCommand({command:'rate',rate});}
 play(){nativeCommand({command:'play'});return Promise.resolve();}
 pause(){nativeCommand({command:'pause'});}
 receive(data){const prior=this.state.paused;this.state={...this.state,...data};
  if(prior!==this.state.paused)this.dispatchEvent(new Event(this.state.paused?'pause':'play'));
  this.dispatchEvent(new Event(data.event));
 }
}
