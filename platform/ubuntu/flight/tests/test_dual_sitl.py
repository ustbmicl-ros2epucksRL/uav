from contextlib import nullcontext

from app import dual_sitl


def test_replan_checks_current_airspace_and_pair(monkeypatch):
    space={'revision':1,'zones':[]}
    monkeypatch.setattr(dual_sitl.business,'connect',lambda:nullcontext(None))
    monkeypatch.setattr(dual_sitl.incidents,'airspace',lambda db:space)
    starts=[[8,8,40],[8,88,40]]
    goals=[[48,8,40],[48,88,40]]
    assert dual_sitl.validate(starts,goals,[])['passed']
    space.update(revision=2,zones=[dict(id='TEMP',x=32,y=5,w=2,d=5,z_min=20,z_max=80)])
    denied=dual_sitl.validate(starts,goals,[])
    assert not denied['passed'] and denied['revision']==2 and '禁飞区或建筑' in denied['reasons']
    space['zones'].clear()
    crossing=dual_sitl.validate(starts,list(reversed(goals)),[])
    assert not crossing['passed'] and crossing['separation_m']==0
