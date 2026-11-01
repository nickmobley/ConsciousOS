import {createServer} from 'node:http';import {createClient} from 'redis';import {readFileSync} from 'node:fs';
const base=new URL('../redis/',import.meta.url),lua=Object.fromEntries(['enqueue','claim','complete'].map(n=>[n,readFileSync(new URL(n+'.lua',base),'utf8')]));
const endpoint='https://fleet-zero-isolated-probe.vercel.app/api/integrated';
let report={status:'starting',at:new Date().toISOString()};
async function main(){
 const c=createClient({url:process.env.REDIS_URL||'redis://red-dbjhc8ou01pc73fflaqg:6379',socket:{connectTimeout:8000,reconnectStrategy:false}});
 try{
 await c.connect();
 const source=await fetch(endpoint,{signal:AbortSignal.timeout(15000)});if(!source.ok)throw Error('durable task endpoint '+source.status);
 const data=await source.json();const task=data.task;if(!task||task.id!=='fleet-zero-integrated-v1'||task.kind!=='uppercase')throw Error('unexpected task');
 const id=task.id,p='fz:{queue}:pending',l='fz:{queue}:leases',prefix='fz:{queue}:job:',j=prefix+id,now=Date.now();
 const run=(name,keys,args)=>c.eval(lua[name],{keys,arguments:args.map(String)});
 if(data.completed){report={status:'already-completed',receipt:data.receipt};return}
 await run('enqueue',[p,j],[id,now,3]);
 const claim=await run('claim',[p,l],['render-redis-worker',now,60000,prefix]);
 if(claim?.[0]!==id)throw Error('claim denied '+JSON.stringify(claim));
 const output=task.input.toUpperCase();
 const result=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({taskId:id,output,fence:Number(claim[1]),worker:'render-redis-worker'}),signal:AbortSignal.timeout(15000)});
 const persisted=await result.json();if(!result.ok||!persisted.valid)throw Error('durable receipt failed '+JSON.stringify(persisted));
 const completion=await run('complete',[j,l],[id,'render-redis-worker',claim[1],Date.now()]);
 const final=await (await fetch(endpoint)).json();
 const checks={durableTaskRead:true,redisAtomicClaim:claim[0]===id,workerExecuted:output==='FLEET ZERO INTEGRATED',privateReceiptPersisted:persisted.valid,redisCompleted:Number(completion[0])===1,durableReadback:final.receipt?.taskId===id&&final.receipt?.output===output};
 report={status:Object.values(checks).every(Boolean)?'passed':'failed',checks,taskId:id,receipt:final.receipt,at:new Date().toISOString()};
 }catch(e){report={status:'failed',error:String(e.message).slice(0,350),at:new Date().toISOString()}}
 finally{if(c.isOpen)await c.quit()}
}
createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');res.end(JSON.stringify(report))}).listen(Number(process.env.PORT||10000),'0.0.0.0');main();
