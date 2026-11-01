import {createServer} from 'node:http';import {createClient} from 'redis';import {readFileSync} from 'node:fs';import {randomUUID} from 'node:crypto';
const root=new URL('../redis/',import.meta.url);const lua=Object.fromEntries(['enqueue','claim','complete'].map(n=>[n,readFileSync(new URL(n+'.lua',root),'utf8')]));
let report={status:'starting'};async function main(){const url=process.env.REDIS_URL||'redis://red-dbjhc8ou01pc73fflaqg:6379',p='fz:{queue}:pending',l='fz:{queue}:leases',prefix='fz:{queue}:job:',id='race-'+randomUUID(),j=prefix+id,clients=[];
try{for(let i=0;i<16;i++){const c=createClient({url,socket:{connectTimeout:8000,reconnectStrategy:false}});await c.connect();clients.push(c)}
const run=(c,n,k,a)=>c.eval(lua[n],{keys:k,arguments:a.map(String)});
const now=Date.now();await run(clients[0],'enqueue',[p,j],[id,now,3]);
const results=await Promise.all(clients.map((c,i)=>run(c,'claim',[p,l],['racer-'+i,now,60000,prefix])));
const winners=results.map((r,i)=>({r,i})).filter(x=>Array.isArray(x.r)&&x.r[0]===id);
const winner=winners[0];const checks={singleWinner:winners.length===1,allOthersDenied:results.filter(x=>x===null).length===15};
if(winner){const stale=await run(clients[(winner.i+1)%16],'complete',[j,l],[id,'racer-'+((winner.i+1)%16),winner.r[1],now+100]);const done=await run(clients[winner.i],'complete',[j,l],[id,'racer-'+winner.i,winner.r[1],now+200]);checks.loserCannotComplete=Number(stale[0])===0;checks.winnerCanComplete=Number(done[0])===1;checks.finalCompleted=(await clients[0].hGet(j,'state'))==='completed'}
report={status:Object.values(checks).every(Boolean)?'passed':'failed',checks,contenders:16,winners:winners.length,jobId:id,at:new Date().toISOString()};await clients[0].del(j)
}catch(e){report={status:'failed',error:e.message}}finally{await Promise.all(clients.map(c=>c.isOpen?c.quit():Promise.resolve()))}}
createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');res.end(JSON.stringify(report))}).listen(Number(process.env.PORT||10000),'0.0.0.0');main();
