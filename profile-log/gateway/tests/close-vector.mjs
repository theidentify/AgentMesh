import './setup.mjs';
import { FakeR2 } from './fakes.mjs';
import { wire } from '../src/encoding.js';
import { secretWindow } from '../src/registry.js';
let input='';for await(const chunk of process.stdin)input+=chunk;
const v=JSON.parse(input),STORAGE=new FakeR2();
for(const [key,value]of Object.entries(v.records))await STORAGE.put(key,wire(value));
process.stdout.write(JSON.stringify(await secretWindow({STORAGE,ACCOUNT_PUBLIC_RECORD:v.account},v.app,v.at)===null));
