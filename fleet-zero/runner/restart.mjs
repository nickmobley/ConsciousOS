import {createServer} from 'node:http';
import {createClient} from 'redis';
import {readFileSync} from 'node:fs';
const base=new URL('../redis/',import.meta.url);
const scripts=Object.fromEntries(['enqueue','claim','recover','complete'].map(n=>[n,readFileSync(new URL(n+'.lua',base),'utf8')]));
const c=createClient({url:process.env.REDIS_URL||'redis://red-dbjhc8ou01pc73fflaqg:6379',socket:{connectTimeout:8000,reconnectStrategy:false}});
const id='fleet-zero-real-crash-recovery-v1',prefix='fz:{queue}:job:',job=prefix+id,pending='fz:{queue}:pending',leases='fz:{queue}:leases',phase='fz:{queue}:crash-phase-v1';
let report={status:'starting'};
async function main(){
 try{
 await c.connect();
 const run=(name,keys,args)=>c.eval(scripts[name],{keys,arguments:args.map(String)});
 const phaseValue=await c.get(phase), now=Date.now();
 if(!phaseValue){
  await c.del(job);await c.lRem(pending,0,id);await c.zRem(leases,id);
  await run('enqueue',[pending,job],[id,now,3]);
  const first=await run('claim',[pending,leases],['crashing-worker',now,1000,prefix]);
  if(first?.[0]!==id)throw Error('initial claim failed');
  await c.set(phase,JSON.stringify({firstFence:first[1],firstWorker:'crashing-worker',firstClaimAt:now}));
  await c.quit();
  process.stderr.write('INTENTIONAL PROCESS CRASH AFTER REAL REDIS CLAIM '+id+'\\n');
  process.exit(42);
 }
 const first=JSON.parse(phaseValue);
 const remaining=Math.max(0,first.firstClaimAt+1100-Date.now());
 if(remaining)await new Promise(resolve=>setTimeout(resolve,remaining));
 const recovered=await run('recover',[job,leases,pending],[id,Date.now()]);
 const stale=await run('complete',[job,leases],[id,'crashing-worker',first.firstFence,Date.now()]);
 const next=await run('claim',[pending,leases],['recovery-worker',Date.now(),60000,prefix]);
 const done=next?.[0]===id?await run('complete',[job,leases],[id,'recovery-worker',next[1],Date.now()]):null;
 const final=await c.hGet(job,'state');
 const checks={processRestartObserved:true,leaseRecovered:recovered?.[1]==='requeued',staleWorkerRejected:Number(stale?.[0])===0,newWorkerClaimed:next?.[0]===id,fenceAdvanced:Number(next?.[1])>Number(first.firstFence),completed:Number(done?.[0])===1,finalState:final==='completed'};
 report={status:Object.values(checks).every(Boolean)?'passed':'failed',checks,firstClaimAt:first.firstClaimAt,recoveredAt:Date.now(),jobId:id,warning:'Redis volatile; durable Postgres ledger not integrated'};
 }catch(e){report={status:'failed',error:String(e.message)}}
 finally{if(c.isOpen)await c.quit();}
}
createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');res.end(JSON.stringify(report))}).listen(Number(process.env.PORT||10000),'0.0.0.0');
main();
