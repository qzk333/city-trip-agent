from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings
from app.database import Base

target_metadata = Base.metadata
database_url = get_settings().database_url

if context.is_offline_mode():
    context.configure(url=database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
