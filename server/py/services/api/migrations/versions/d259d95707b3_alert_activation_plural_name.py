# Copyright 2024 Iguazio
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""alert_activation_plural_name

Revision ID: d259d95707b3
Revises: aaa213106ec5
Create Date: 2024-12-09 13:47:42.398038

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "d259d95707b3"
down_revision = "aaa213106ec5"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###

    check_table_sql = """
            SHOW TABLES LIKE 'alert_activation';
        """
    result = op.get_bind().execute(check_table_sql).fetchone()

    if result:
        # Rename table only if it exists
        rename_table_sql = """
                RENAME TABLE alert_activation TO alert_activations;
            """
        op.execute(rename_table_sql)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    pass
    # ### end Alembic commands ###
