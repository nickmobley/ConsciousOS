import {createServer} from 'node:http';
import {createClient} from 'redis';
import {readFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
const root=new URL('../redis/',import.meta.url);
const scripts=Object.fromEntries(['enqueue','claim','recover','complete'].map(n=>[n,readFileSync(new URL(n+'.lua',root),'utf8')]));
let report={status:'starting',at:new Date().toISOString()};
async function test(){
 const client=createClient({url:process.env.REDIS_URL||'redis://red-dbjhc8ou01pc73fflaqg:6379',socket:{connectTimeout:8000,reconnectStrategy:false}});
 try{
  await client.connect();
  const id='probe-'+randomUUID(), prefix='fz:{queue}:job:', job=prefix+id,pending='fz:{queue}:pending',leases='fz:{queue}:leases';
  const now=Date.now();
  const evalLua=(name,keys,args)=>client.eval(scripts[name],{keys,arguments:args.map(String)});
  const checks={};
  const en=await evalLua('enqueue',[pending,job],[id,now,3]);
  const dup=await evalLua('enqueue',[pending,job],[id,now,3]);
  checks.idempotent=Number(en[0])===1&&Number(dup[0])===0;
  const a=await evalLua('claim',[pending,leases],['worker-a',now,1000,prefix]);
  checks.firstClaim=Array.isArray(a)&&a[0]===id;
  const b=await evalLua('claim',[pending,leases],['worker-b',now,1000,prefix]);
  checks.noDoubleClaim=b===null;
  const recovered=await evalLua('recover',[job,leases,pending],[id,now+1001]);
  checks.recovered=Number(recovered[0])===1&&recovered[1]==='requeued';
  const stale=await evalLua('complete',[job,leases],[id,'worker-a',a[1],now+1002]);
  checks.staleRejected=Number(stale[0])===0;
  const c=await evalLua('claim',[pending,leases],['worker-b',now+1002,1000,prefix]);
  checks.reassigned=Array.isArray(c)&&c[0]===id&&Number(c[1])>Number(a[1]);
  const done=await evalLua('complete',[job,leases],[id,'worker-b',c[1],now+1003]);
  checks.completed=Number(done[0])===1;
  const state=await client.hGet(job,'state');
  checks.finalState=state==='completed';
  report={status:Object.values(checks).every(Boolean)?'passed':'failed',checks,jobId:id,at:new Date().toISOString(),note:'Redis volatile on free tier; durable source-of-truth and real browser worker not connected'};
  await client.del(job);
 }catch(e){report={status:'failed',error:String(e.message).slice(0,300),at:new Date().toISOString()};}
 finally{if(client.isOpen)await client.quit();}
}
createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');res.end(JSON.stringify(report));}).listen(Number(process.env.PORT||10000),'0.0.0.0');
test();
