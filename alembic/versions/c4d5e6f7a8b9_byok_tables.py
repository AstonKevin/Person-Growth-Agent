"""byok provider and usage tables

Revision ID: c4d5e6f7a8b9
Revises: deba3df09754
Create Date: 2026-10-19 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'deba3df09754'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # user 表加角色/启用状态：存量行必须给默认值，否则加 NOT NULL 列会失败
    op.add_column('user',
        sa.Column('role', sa.String(length=20), nullable=False, server_default='user'))
    op.add_column('user',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('1')))

    # 用户自带 LLM 配置（每用户 1 条）
    op.create_table('user_provider',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False, server_default='openai'),
        sa.Column('base_url', sa.String(length=255), nullable=False),
        sa.Column('encrypted_api_key', sa.Text(), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('verified', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('last_verified_at', sa.DateTime(), nullable=True),
        sa.Column('create_time', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('update_time', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_user_provider_user_id'),
        'user_provider', ['user_id'], unique=True)

    # LLM 用量审计
    op.create_table('usage_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_usage_log_user_id'),
        'usage_log', ['user_id'], unique=False)
    op.create_index(op.f('ix_usage_log_created_at'),
        'usage_log', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    # MySQL 外键依赖其索引：必须直接删表（表删除时自动移除索引与外键），
    # 不能先 drop 外键列上的索引（会报 1553）。
    op.drop_table('usage_log')
    op.drop_table('user_provider')
    op.drop_column('user', 'is_active')
    op.drop_column('user', 'role')
