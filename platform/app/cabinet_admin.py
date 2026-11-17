"""Offline account/organisation administration. Run with the service stopped."""
import argparse
import fcntl
import getpass
from . import cabinet_demo as api
from . import cabinet_auth as auth


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('migrate-auth')
    commands.add_parser('migrate-templates')
    commands.add_parser('migrate-attachments')
    commands.add_parser('migrate-chunks')
    check=commands.add_parser('check-objects');check.add_argument('--probe',action='store_true')
    inline=commands.add_parser('migrate-inline-attachments');inline.add_argument('--apply',action='store_true')
    move=commands.add_parser('migrate-object-store');move.add_argument('--target',required=True);move.add_argument('--apply',action='store_true')
    migrate = commands.add_parser('migrate-demo-keys')
    migrate.add_argument('--source-checkpoint', required=True)
    org = commands.add_parser('add-org'); org.add_argument('--name', required=True)
    user = commands.add_parser('add-user')
    user.add_argument('--org', required=True); user.add_argument('--username', required=True)
    user.add_argument('--role', required=True, choices=auth.ROLES)
    org_state=commands.add_parser('set-org')
    org_state.add_argument('--org', required=True); org_state.add_argument('--enabled', choices=['yes','no'], required=True)
    reset=commands.add_parser('reset-password'); reset.add_argument('--username',required=True)
    args = parser.parse_args()
    if args.command=='check-objects':
        import json
        from object_operations import check
        print(json.dumps(check(probe=args.probe),ensure_ascii=False));return
    folder=api.store.DATA/'cabinet-demo'; folder.mkdir(parents=True, exist_ok=True)
    with open(folder/'platform.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == 'migrate-demo-keys':
            from . import cabinet_key_migration as migration
            from . import cabinet_keys as keys
            if keys.selected_mode() != 'hardware':
                raise ValueError('Select hardware mode for migration')
            source = api.demo_guard(args.source_checkpoint)
            target = keys.hardware_guard()
            print(migration.migrate(folder, source, target))
            return
        guard=api.configured_guard()
        if guard is None: raise ValueError('Independent checkpoint configuration required')
        cab=api.persistence.open_cabinet(folder,guard)
        if args.command=='migrate-auth':
            auth.install(cab); print('Account schema installed; legacy demo sessions invalidated.'); return
        auth.require_schema(cab)
        if args.command=='migrate-templates':
            import template_registry
            template_registry.install(cab); print('Template registry installed without changing legacy assets.'); return
        if args.command=='migrate-attachments':
            import media_attachments
            media_attachments.install(cab); print('Encrypted attachment table installed.'); return
        if args.command=='migrate-chunks':
            import chunked_attachments
            chunked_attachments.install(cab); print('Checkpointed chunk and stream tables installed.'); return
        if args.command in ('migrate-inline-attachments','migrate-object-store'):
            import json
            from object_operations import move_inline,move_store
            result=move_inline(cab,args.apply) if args.command=='migrate-inline-attachments' else move_store(cab,args.target,args.apply)
            print(json.dumps(result,ensure_ascii=False));return
        if args.command=='add-org': print(auth.add_org(cab,args.name))
        elif args.command=='add-user':
            password=getpass.getpass('Initial password (12–128 characters): ')
            if password!=getpass.getpass('Confirm password: '): raise ValueError('Passwords differ')
            print(auth.add_user(cab,args.org,args.username,password,args.role))
        elif args.command=='set-org':
            with cab._transaction() as db:
                if not db.execute('SELECT 1 FROM auth_orgs WHERE id=?',(args.org,)).fetchone(): raise ValueError('Unknown org')
                db.execute('UPDATE auth_orgs SET enabled=? WHERE id=?',(int(args.enabled=='yes'),args.org))
                # Re-enabling an org must not revive its previous sessions.
                db.execute('UPDATE auth_users SET version=version+1 WHERE org=?',(args.org,))
                cab._audit(db,'org_enabled','operator',org=args.org,enabled=args.enabled=='yes')
        elif args.command=='reset-password':
            import secrets
            password=getpass.getpass('New password: ')
            if password!=getpass.getpass('Confirm password: '): raise ValueError('Passwords differ')
            auth.validate_password(password)
            salt=secrets.token_bytes(16); hashed=auth.password_hash(password,salt)
            with cab._transaction() as db:
                row=db.execute('SELECT id FROM auth_users WHERE username=?',(auth.username(args.username),)).fetchone()
                if row is None: raise ValueError('Unknown user')
                db.execute('UPDATE auth_users SET salt=?,password=?,version=version+1 WHERE id=?',(salt,hashed,row['id']))
                cab._audit(db,'password_reset','operator',subject=row['id'])


if __name__=='__main__': main()
