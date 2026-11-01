import {createServer} from 'node:http';import {createClient} from 'redis';import {readFileSync} from 'node:fs';
const base=new URL('../redis/',import.meta.url),lua=Object.fromEntries(['enqueue','claim','complete'].map(n=>[n,readFileSync(new URL(n+'.lua',base),'utf8')]));
const endpoint='https://fleet-zero-isolated-probe.vercel.app/api/supervisor';
const id='fleet-zero-supervised-v2',j='fz:{supervisor}:job:'+id,p='fz:{supervisor}:pending',l='fz:{supervisor}:leases',prefix='fz:{supervisor}:job:';
let state={status:'starting',polls:0,checks:{}},busy=false;
const c=createClient({url:process.env.REDIS_URL||'redis://red-dbjhc8ou01pc73fflaqg:6379',socket:{connectTimeout:8000,reconnectStrategy:false}});
const run=(n,k,a)=>c.eval(lua[n],{keys:k,arguments:a.map(String)});
async function tick(){if(busy)return;busy=true;try{
state.polls++;const response=await fetch(endpoint,{signal:AbortSignal.timeout(12000)});if(!response.ok)throw Error('durable endpoint '+response.status);const data=await response.json();
if(data.task?.id!==id)throw Error('wrong durable task');
const existing=await c.hGetAll(j);
if(data.receipt?.taskId===id){
 if(existing.state!=='completed'){
 await c.hSet(j,{id,state:'completed',fence:String(data.receipt.fence),worker:data.receipt.worker,attempt:'1',max_attempts:'3',reconciled_from:'durable_receipt',updated_ms:String(Date.now())});
 }
 const final=await c.hGetAll(j);
 state={status:'completed',polls:state.polls,checks:{durableReceipt:true,redisCompleted:final.state==='completed',fenceMatches:Number(final.fence)===data.receipt.fence},reconciled:existing.state!=='completed',at:new Date().toISOString()};return;
}
if(existing.state==='completed')throw Error('Redis completed but durable receipt missing');
if(!existing.state)await run('enqueue',[p,j],[id,Date.now(),3]);
const claimed=await run('claim',[p,l],['fleet-zero-supervisor',Date.now(),30000,prefix]);
if(claimed?.[0]!==id){state.status='waiting';return}
const output=data.task.input.toUpperCase();
const result=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({taskId:id,worker:'fleet-zero-supervisor',fence:Number(claimed[1]),output}),signal:AbortSignal.timeout(12000)});
const saved=await result.json();if(!result.ok||!saved.valid)throw Error('receipt write failed '+JSON.stringify(saved));
const completed=await run('complete',[j,l],[id,'fleet-zero-supervisor',claimed[1],Date.now()]);
state={status:Number(completed[0])===1?'completed':'failed',polls:state.polls,checks:{claimed:true,executed:output==='AUTONOMOUS RECOVERY VERIFIED',receiptStored:saved.valid,redisCompleted:Number(completed[0])===1},at:new Date().toISOString()};
}catch(e){state={...state,status:'retrying',error:String(e.message).slice(0,200),at:new Date().toISOString()}}finally{busy=false}}
createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');res.end(JSON.stringify({...state,mode:'continuous polling every 10s',taskId:id}))}).listen(Number(process.env.PORT||10000),'0.0.0.0');
c.connect().then(()=>{tick();setInterval(tick,10000)}).catch(e=>{state={status:'failed',error:e.message}});
