import { PGlite } from '../work/pgcheck/node_modules/@electric-sql/pglite/dist/index.js';
import { PGLiteSocketServer } from '../work/pgcheck/node_modules/@electric-sql/pglite-socket/dist/index.js';
import { createServer } from 'node:http';
const db = new PGlite('./work/pgcheck/pgdata');
await db.waitReady;
const server = new PGLiteSocketServer({db,port:55432,host:'127.0.0.1'});
await server.start();
const control=createServer(async(req,res)=>{
 if(req.url!='/shutdown'){res.writeHead(404).end();return;}
 res.end('stopping');
 await server.stop();
 await db.close();
 control.close();
});
control.listen(55433,'127.0.0.1');
console.log('Local PostgreSQL test engine ready on 127.0.0.1:55432');
