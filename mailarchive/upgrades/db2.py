from trac.db import Table, Column, Index, DatabaseManager


new_table = Table('mailarchive', key='id')[
        Column('id'),
        Column('subject'),
        Column('fromheader'),
        Column('toheader'),
        Column('date', type='int64'),
        Column('body'),
        Column('allheaders'),
        Column('comment'),
        Index(['date']),
    ]


def do_upgrade(env, ver, cursor):
    cursor.execute("CREATE TEMPORARY TABLE mailarchive_old AS SELECT * FROM mailarchive")
    cursor.execute("DROP TABLE mailarchive")

    DatabaseManager(env).create_tables([new_table])

    cursor.execute("""
        INSERT INTO mailarchive (id, subject, fromheader, toheader, date, body, allheaders, comment)
        SELECT o.id, o.subject, o.fromheader, o.toheader, o.date, o.body, o.allheaders, ''
        FROM mailarchive_old o
        """)
    cursor.execute("DROP TABLE mailarchive_old")
