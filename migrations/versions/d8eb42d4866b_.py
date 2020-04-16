"""Add torrent_file table.

Revision ID: d8eb42d4866b
Revises: 7e246705da6a
Create Date: 2020-04-15 23:41:19.340336

"""

# revision identifiers, used by Alembic.
revision = "d8eb42d4866b"
down_revision = "7e246705da6a"

import sqlalchemy as sa
from alembic import op


def upgrade():
  # ### commands auto generated by Alembic - please adjust! ###
  op.create_table(
      "torrent_file",
      sa.Column("id", sa.Integer(), nullable=False),
      sa.Column("file_id", sa.Integer(), nullable=True),
      sa.Column("hashed", sa.Boolean(), nullable=True),
      sa.Column("magnet", sa.UnicodeText(), nullable=True),
      sa.PrimaryKeyConstraint("id"),
  )
  # ### end Alembic commands ###


def downgrade():
  # ### commands auto generated by Alembic - please adjust! ###
  op.drop_table("torrent_file")
  # ### end Alembic commands ###
