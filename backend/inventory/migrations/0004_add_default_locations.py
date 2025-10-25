# Data migration to add default locations for new makerspaces

from django.db import migrations


def create_default_locations(apps, schema_editor):
    """Create sensible default locations for a typical makerspace."""
    Location = apps.get_model('inventory', 'Location')

    default_locations = [
        {
            'name': 'Main Workshop',
            'description': 'Primary workspace for general projects and assembly'
        },
        {
            'name': 'Electronics Lab',
            'description': 'ESD-safe workspace for electronics work and soldering'
        },
        {
            'name': 'Wood Shop',
            'description': 'Woodworking area with saws, sanders, and hand tools'
        },
        {
            'name': 'Metal Shop',
            'description': 'Metal fabrication area with welding and machining equipment'
        },
        {
            'name': '3D Printing Area',
            'description': '3D printers and filament storage'
        },
        {
            'name': 'Tool Crib',
            'description': 'Shared hand tools and small equipment'
        },
        {
            'name': 'Storage Room',
            'description': 'General supplies and material storage'
        },
        {
            'name': 'Office Supplies',
            'description': 'Paper, pens, tape, and general office items'
        },
        {
            'name': 'Safety Equipment',
            'description': 'PPE, first aid, fire extinguishers, and safety gear'
        },
        {
            'name': 'Consumables',
            'description': 'Sandpaper, shop rags, solvents, and disposable items'
        },
    ]

    # Create default locations (skip if they already exist by name)
    for loc_data in default_locations:
        Location.objects.get_or_create(
            name=loc_data['name'],
            defaults={'description': loc_data['description']}
        )


def remove_default_locations(apps, schema_editor):
    """Remove default locations on reverse migration."""
    Location = apps.get_model('inventory', 'Location')

    default_names = [
        'Main Workshop',
        'Electronics Lab',
        'Wood Shop',
        'Metal Shop',
        '3D Printing Area',
        'Tool Crib',
        'Storage Room',
        'Office Supplies',
        'Safety Equipment',
        'Consumables',
    ]

    # Only remove default locations, not user-created ones
    Location.objects.filter(
        name__in=default_names,
        description__startswith='Primary workspace'
    ).delete() | Location.objects.filter(
        name__in=default_names,
        description__startswith='ESD-safe workspace'
    ).delete() | Location.objects.filter(
        name__in=default_names,
        description__startswith='Woodworking area'
    ).delete() | Location.objects.filter(
        name__in=default_names,
        description__startswith='Metal fabrication'
    ).delete() | Location.objects.filter(
        name__in=default_names,
        description__startswith='3D printers'
    ).delete() | Location.objects.filter(
        name__in=default_names,
        description__startswith='Shared hand tools'
    ).delete() | Location.objects.filter(
        name__in=default_names,
        description__startswith='General supplies'
    ).delete() | Location.objects.filter(
        name__in=default_names,
        description__startswith='Paper, pens'
    ).delete() | Location.objects.filter(
        name__in=default_names,
        description__startswith='PPE, first aid'
    ).delete() | Location.objects.filter(
        name__in=default_names,
        description__startswith='Sandpaper, shop rags'
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0003_add_location_model_and_multi_supplier'),
    ]

    operations = [
        migrations.RunPython(create_default_locations, reverse_code=remove_default_locations),
    ]
