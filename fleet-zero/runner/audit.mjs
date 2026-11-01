import {createServer} from 'node:http';import {createClient} from 'redis';
const endpoint='https://fleet-zero-isolated-probe.vercel.app/api/integrated';
let report={status:'starting'};
async function run(){
 const c=createClient({url:process.env.REDIS_URL||'redis://red-dbjhc8ou01pc73fflaqg:6379',socket:{connectTimeout:8000,reconnectStrategy:false}});
 try{
  const a=await (await fetch(endpoint,{signal:AbortSignal.timeout(15000)})).json();
  const b=await (await fetch(endpoint,{signal:AbortSignal.timeout(15000)})).json();
  await c.connect();
  const id='fleet-zero-integrated-v1';
  const state=await c.hGetAll('fz:{queue}:job:'+id);
  const checks={durableTaskReadable:a.task?.id===id,receiptReadableTwice:a.receipt?.taskId===id&&b.receipt?.taskId===id,receiptStable:JSON.stringify(a.receipt)===JSON.stringify(b.receipt),outputValid:a.receipt?.output==='FLEET ZERO INTEGRATED',redisCompleted:state.state==='completed',fenceAgrees:Number(state.fence)===a.receipt?.fence};
  report={status:Object.values(checks).every(Boolean)?'passed':'failed',checks,taskId:id,redisState:state.state||'missing',at:new Date().toISOString(),scope:'Non-destructive reconciliation audit; no mutations or credentials exposed'};
 }catch(e){report={status:'failed',error:String(e.message).slice(0,200)}}
 finally{if(c.isOpen)await c.quit()}
}
createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');res.end(JSON.stringify(report))}).listen(Number(process.env.PORT||10000),'0.0.0.0');run();
