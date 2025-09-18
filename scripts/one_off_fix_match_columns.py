"""add timestamp column to wallet_transactions

Revision ID: add_wallet_tx_timestamp
Revises: <put_previous_revision_here>
Create Date: 2025-02-18

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = "add_wallet_tx_timestamp"
down_revision = "<put_previous_revision_here>" # change to your last migration revision ID
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wallet_transactions",
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("wallet_transactions", "timestamp")
