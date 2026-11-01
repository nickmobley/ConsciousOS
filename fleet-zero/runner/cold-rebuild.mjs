import {createServer} from 'node:http';import {createClient} from 'redis';import {readFileSync} from 'node:fs';
const base=new URL('../redis/',import.meta.url);const lua=Object.fromEntries(['enqueue','claim','complete'].map(n=>[n,readFileSync(new URL(n+'.lua',base),'utf8')]));
let report={status:'starting'};async function main(){
const c=createClient({url:process.env.REDIS_URL||'redis://red-dbjhc8ou01pc73fflaqg:6379',socket:{connectTimeout:8000,reconnectStrategy:false}});
try{
const durable=await(await fetch('https://fleet-zero-isolated-probe.vercel.app/api/integrated')).json();
if(!durable.task?.id||!durable.receipt?.taskId)throw Error('durable task or receipt missing');
await c.connect();
const id=durable.task.id,job='fz:{rebuild}:job:'+id,pending='fz:{rebuild}:pending',leases='fz:{rebuild}:leases';
const before=await c.exists(job);
if(before)throw Error('rebuild namespace already initialized; test must start with empty state');
const receipt=durable.receipt;
await c.hSet(job,{id,state:'completed',fence:String(receipt.fence),worker:receipt.worker,attempt:'1',max_attempts:'3',reconciled_from:'private_blob',updated_ms:String(Date.now())});
const state=await c.hGetAll(job);
const claim=await c.eval(lua.claim,{keys:[pending,leases],arguments:['untrusted-worker',String(Date.now()),'1000','fz:{rebuild}:job:']});
const checks={durableTaskRead:durable.task.id===id,durableReceiptRead:receipt.taskId===id,startedWithoutRedisTask:before===0,reconstructedCompletedState:state.state==='completed',fencePreserved:Number(state.fence)===receipt.fence,completedTaskNotClaimable:claim===null};
report={status:Object.values(checks).every(Boolean)?'passed':'failed',checks,taskId:id,at:new Date().toISOString(),scope:'Non-destructive cold rebuild into isolated empty Redis namespace; no original state altered',limitation:'Completed task rebuild only; not full Redis restart or pending task recovery'};
}catch(e){report={status:'failed',error:String(e.message).slice(0,220)}}finally{if(c.isOpen)await c.quit()}}
createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');res.end(JSON.stringify(report))}).listen(Number(process.env.PORT||10000),'0.0.0.0');main();
