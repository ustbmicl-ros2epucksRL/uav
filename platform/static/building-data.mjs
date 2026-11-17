// WGS84 footprints; all height provenance is retained, never inferred as surveyed.
export function normalizeBuildings(data){
 if(data?.type!=='FeatureCollection'||!Array.isArray(data.features)||data.features.length>5000)throw Error('需要不超过 5000 栋的 WGS84 GeoJSON FeatureCollection');
 return data.features.flatMap((f,i)=>{
  const p=f.properties||{},g=f.geometry;
  if(!g||!['Polygon','MultiPolygon'].includes(g.type))throw Error(`建筑 ${i+1} 必须是 Polygon 或 MultiPolygon`);
  const number=v=>v===undefined||v===null||v===''?NaN:Number(String(v).replace(/\s*m\s*$/i,''));
  const tagged=number(p.height),levels=number(p['building:levels']??p.levels);
  const h=Number.isFinite(tagged)?tagged:Number.isFinite(levels)?levels*3:9;
  if(h<=0||h>1000)throw Error(`建筑 ${i+1} 高度须在 0–1000 米内`);
  const estimated=!Number.isFinite(tagged)||p.height_estimated===true;
  const source=Number.isFinite(tagged)?(p.height_estimated===true?String(p.height_source||'数据集模型估计（非测绘）'):'数据标注高度（未测绘核验）'):Number.isFinite(levels)?'楼层数 × 3 米估算':'缺失高度，按 9 米示意';
  const polygons=g.type==='Polygon'?[g.coordinates]:g.coordinates;
  if(!Array.isArray(polygons)||polygons.length>100)throw Error('建筑几何过大');
  return polygons.map((rings,j)=>{
   if(!Array.isArray(rings)||!rings.length||rings.length>100)throw Error('建筑轮廓为空或过大');
   for(const ring of rings){
    if(!Array.isArray(ring)||ring.length<4||ring.length>2000)throw Error('建筑环须含 4–2000 个坐标');
    for(const q of ring)if(!Array.isArray(q)||!Number.isFinite(q[0])||!Number.isFinite(q[1])||Math.abs(q[0])>180||Math.abs(q[1])>85)throw Error('坐标必须为 WGS84 经度、纬度');
    if(ring[0][0]!==ring.at(-1)[0]||ring[0][1]!==ring.at(-1)[1])throw Error('建筑轮廓必须闭合');
   }
   return {id:f.id??`${i}-${j}`,name:String(p.name||`建筑 ${i+1}`),rings,height:h,heightSource:source,estimated};
  });
 });
}
