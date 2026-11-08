// Small DOM stand-in: verify incremental transcript behavior without a browser dependency.
class ElementParent {addEventListener() {}}
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.dataset={}; this.handlers={}; this.parent=null; this.parentElement=new ElementParent(); }
  append(...children) { for (const child of children) {child.parent=this; this.children.push(child);} }
  prepend(child) { child.parent=this; this.children.unshift(child); }
  replaceChildren(...children) { this.children=[]; this.append(...children); }
  remove() { if(this.parent) this.parent.children=this.parent.children.filter(child=>child!==this); }
  addEventListener(name,handler) {this.handlers[name]=handler;}
  setAttribute() {}
  querySelectorAll() {return [];}
}
const elements=new Map(), byId=id=>{
  if(!elements.has(id)) elements.set(id,new Element("div"));
  return elements.get(id);
};
const document={createElement:tag=>new Element(tag),addEventListener() {}};
const sanitize=()=>new Element("fragment");
const source=Deno.readTextFileSync(new URL("../src/teamrunner/ui/static/runs.js",import.meta.url));
const render=new Function("byId","document","DOMPurify","marked","ResizeObserver",source+"\nreturn renderConversation;")(byId,document,{sanitize},{parse:text=>text},class {observe() {}});
const assert=(condition,message)=>{if(!condition) throw new Error(message);};
const target=new Element("section"), data={events:[],members:[],artifacts:[],raw_files:[]};
let state=render(data,target,true);
assert(target.children[0].textContent.includes("Waiting"),"Empty live status missing");
data.events.push({event:"agent_message",seq:1,from:"first",content:"first message",read:[]});
state=render(data,target,true,state);
const first=target.children[0], read=first.children.at(-1);
assert(first.tagName==="article","Waiting placeholder wasn't removed");
assert(read.children.length===1,"Read-context should be lazy");
read.open=true; read.handlers.toggle();
assert(read.children.length===2,"Opening context should populate its content");
for(let i=2;i<=1000;i++) data.events.push({event:"agent_message",seq:i,from:"worker",content:"a reply",read:[1]});
state=render(data,target,true,state);
assert(target.children[0]===first,"Appending events replaced an existing card");
assert(read.open,"Appending events closed a reader's context");
assert(state.count===1000&&state.bySequence.size===1000,"Incremental event index is incomplete");
assert(target.children.length===1000,"Messages duplicated or missing");
render(data,target,false,state);
assert(target.children.length===1000,"Status-only update appended duplicate cards");
const context=target.children.at(-1).children.at(-1);
assert(context.children.length===1,"Unopened context eagerly created DOM");
context.open=true;context.handlers.toggle();
assert(context.children.length===2,"Recorded read entry missing");
console.log("1,000-turn incremental transcript: card identity, open state, lazy context and deduplication passed.");
