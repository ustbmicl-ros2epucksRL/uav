'use strict';
const lab=require('../static/research-lab.js');
try {
 if(process.argv[2]==='catalog') console.log(JSON.stringify(lab.all.map(({run,...s})=>s)));
 else console.log(JSON.stringify(lab.execute(process.argv[2],Number(process.argv[3]))));
} catch(e){console.error(e.message);process.exitCode=1;}
