from app import disposition as d, sim


def aircraft(position=(800,200,100)):
    v=sim.Vehicle('T','test','P',[200,200,0],'报文终端')
    v.position=list(position);v.armed=True;v.mode='LOITER'
    return v


def test_exact_intersection_catches_thin_obstacles_and_grazing():
    box=dict(lower=[.123,0,0],upper=[.124,1,1])
    assert d.intersects([0,0,1],[1,0,1],box)
    assert not d.intersects([0,0,1.001],[1,0,1.001],box)


def test_building_blocks_direct_return_and_verified_detour_executes():
    v=aircraft();buildings=[dict(id='B',x=450,y=100,w=80,d=200,h=160)]
    space=d.default_airspace();report=d.propose(v,'rtl',buildings,space,sim.FENCE)
    assert not report['direct']['passed'] and report['choices']
    choice=report['choices'][0];boxes=d.obstacles(buildings,[])
    sim.command(v,'rtl');v.disposition_route=choice['route'][1:];v.disposition_wp=0;v.disposition_reason='rtl'
    for _ in range(300):
        before=list(v.position);sim.tick(v)
        assert not any(d.intersects(before,v.position,box) for box in boxes)
        if v.done:break
    assert v.done_reason=='rtl' and not v.armed
    assert v.position==v.home


def test_no_fly_zone_cannot_be_overflown_and_is_avoided():
    v=aircraft();space=d.default_airspace();space['zones']=[dict(id='Z',x=450,y=100,w=80,d=200,z_min=0,z_max=300)]
    report=d.propose(v,'rtl',[],space,sim.FENCE)
    assert report['direct']['reasons'][0]['kind']=='no_fly'
    assert report['choices']
    for choice in report['choices']:
        assert d.check(choice['route'],d.obstacles([],space['zones']),sim.FENCE)['passed']
        assert any(p[1]<80 or p[1]>320 for p in choice['route'])


def test_rooftop_landing_uses_a_configured_alternate_site():
    v=aircraft((800,200,180));buildings=[dict(id='roof',x=750,y=150,w=100,d=100,h=80)]
    space=d.default_airspace();report=d.propose(v,'land',buildings,space,sim.FENCE)
    assert not report['direct']['passed']
    assert report['choices']
    assert all(c['route'][-1] in [[s['x'],s['y'],0] for s in space['landing_sites']] for c in report['choices'])


def test_no_safe_route_and_insufficient_battery_never_produce_choice():
    v=aircraft();space=d.default_airspace();space['zones']=[dict(id='all',x=0,y=0,w=2000,d=2000,z_min=0,z_max=300)]
    assert d.propose(v,'land',[],space,sim.FENCE)['no_route']
    v.battery=20
    assert d.propose(v,'rtl',[],d.default_airspace(),sim.FENCE)['no_route']
