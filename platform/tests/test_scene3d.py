import numpy as np
from app.scene3d import first_hit,inspect,RayRequest
from app.engineering import visible
B=[dict(x=100,y=100,w=100,d=100,h=80)]
def test_wall_blocks_horizontal_ray_but_roof_does_not():
    hit=first_hit([0,150,40],[300,150,40],B)
    assert hit['position']==[100,150,40]
    assert first_hit([0,150,40],[300,150,40],B,True) is None

def test_over_roof_and_parallel_outside_are_clear():
    assert first_hit([0,150,90],[300,150,90],B) is None
    assert first_hit([0,90,40],[300,90,40],B) is None

def test_first_hit_matches_planning_visibility():
    d=dict(x=0,y=150,z=40,range=1000,fov=360,azimuth=0,pitch=0,vertical_fov=180,type='RADAR',status='online',id='D1')
    points=np.array([[300,150,40],[300,150,90],[300,90,40]])
    valid=visible(points,d,B)[0]
    assert [first_hit([0,150,40],p,B) is None for p in points]==valid.tolist()
    scene=dict(devices=[d],buildings=B,revision=1)
    assert inspect(scene,RayRequest(sensor_id='D1',target=[300,150,40]))['blocked']
