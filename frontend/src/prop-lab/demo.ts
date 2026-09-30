import type { Performance, VectorPath } from './engine.ts';
const path=(segments:number[][],fill:string,closed=true,stroke='#a88c58',width=1.4):VectorPath=>({segments,fill,closed,stroke,width,opacity:1});
export const DEMO: Performance = {
  title:'漂浮小杯，轻轻喝一口', intent:'手绘连接校准：杯口到嘴边后绕接触点倾斜，再回正离开。角色没有手部，不模拟虚构的抓握。', prop_name:'蓝金陶瓷杯', duration_ms:8000,
  paths:[
    path([[23,12],[46,4,45,40,22,37],[22,31],[36,31,37,13,23,19]],'#e8d7ae'),
    path([[-24,0],[-22,43,-17,55,0,55],[17,55,22,43,24,0]],'#fff8e8'),
    path([[-22,28],[-16,33,16,33,22,28],[20,43,14,51,0,51],[-14,51,-20,43,-22,28]],'#c3def0',true,'#c3def0',.5),
    path([[-24,0],[-24,-7,24,-7,24,0],[24,7,-24,7,-24,0]],'#e2c98e'),
    path([[-20,0],[-20,-4,20,-4,20,0],[20,4,-20,4,-20,0]],'#b9e5f4',true,'#79b5d0',.7),
    path([[-15,12],[-16,19,-15,29,-13,34]],'#fff8e8',false,'#ffffff',2),
    path([[-18,40],[-7,45,7,45,18,40]],'#fff8e8',false,'#c8ad6d',1.5),
  ], frames:[
    {at:0,anchor:'stage',x:110,y:65,angle:0,head_angle:0,opacity:0,label:'出现'},
    {at:.12,anchor:'stage',x:105,y:55,angle:0,head_angle:0,opacity:1,label:'漂浮靠近'},
    {at:.3,anchor:'stage',x:12,y:6,angle:0,head_angle:0,opacity:1,label:'对齐杯口'},
    {at:.4,anchor:'mouth',x:0,y:0,angle:0,head_angle:0,opacity:1,label:'连接嘴部'},
    {at:.55,anchor:'mouth',x:0,y:0,angle:18,head_angle:-4,opacity:1,label:'倾斜喝水'},
    {at:.65,anchor:'mouth',x:0,y:0,angle:18,head_angle:-4,opacity:1,label:'停留喝一口'},
    {at:.76,anchor:'mouth',x:0,y:0,angle:0,head_angle:0,opacity:1,label:'回正'},
    {at:.88,anchor:'stage',x:80,y:55,angle:0,head_angle:0,opacity:1,label:'离开嘴部'},
    {at:1,anchor:'stage',x:110,y:65,angle:0,head_angle:0,opacity:0,label:'收回'},
  ],
};
