package fun.cadacrew.roadledger;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

public final class GarageDb extends SQLiteOpenHelper {
    private static final String DATABASE_NAME = "roadledger.db";
    private static final int VERSION = 1;

    public GarageDb(Context context) {
        super(context, DATABASE_NAME, null, VERSION);
    }

    @Override public void onConfigure(SQLiteDatabase db) {
        super.onConfigure(db);
        db.setForeignKeyConstraintsEnabled(true);
    }

    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE vehicles (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, make TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '')");
        db.execSQL("CREATE TABLE fuel (id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE, date INTEGER NOT NULL, odometer REAL NOT NULL CHECK(odometer>=0), liters REAL NOT NULL CHECK(liters>0), cost REAL NOT NULL CHECK(cost>=0), full_tank INTEGER NOT NULL CHECK(full_tank IN (0,1)))");
        db.execSQL("CREATE TABLE expenses (id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE, date INTEGER NOT NULL, category TEXT NOT NULL, amount REAL NOT NULL CHECK(amount>=0), odometer REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '')");
        db.execSQL("CREATE TABLE trips (id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE, date INTEGER NOT NULL, start_km REAL NOT NULL, end_km REAL NOT NULL, note TEXT NOT NULL DEFAULT '', CHECK(end_km>=start_km))");
        db.execSQL("CREATE INDEX idx_fuel_vehicle_odo ON fuel(vehicle_id,odometer)");
        db.execSQL("CREATE INDEX idx_expenses_vehicle_date ON expenses(vehicle_id,date)");
        db.execSQL("CREATE INDEX idx_trips_vehicle_date ON trips(vehicle_id,date)");
    }
    @Override public void onUpgrade(SQLiteDatabase db,int oldVersion,int newVersion) {
        // No schema migration needed for the first published version.
        if (oldVersion != 1) throw new IllegalStateException("Unsupported database version");
    }

    public static final class Vehicle {
        public final long id;
        public final String name,make,model;
        Vehicle(long id,String name,String make,String model) { this.id=id; this.name=name; this.make=make; this.model=model; }
    }
    public static final class Fuel {
        public final long id,vehicleId,date;
        public final double odometer,liters,cost;
        public final boolean full;
        Fuel(long id,long vehicleId,long date,double odo,double liters,double cost,boolean full) {
            this.id=id; this.vehicleId=vehicleId; this.date=date; this.odometer=odo; this.liters=liters; this.cost=cost; this.full=full;
        }
    }
    public static final class Expense {
        public final long id,vehicleId,date;
        public final double amount,odometer;
        public final String category,note;
        Expense(long id,long vehicleId,long date,String category,double amount,double odometer,String note) {
            this.id=id;this.vehicleId=vehicleId;this.date=date;this.category=category;this.amount=amount;this.odometer=odometer;this.note=note;
        }
    }
    public static final class Trip {
        public final long id,vehicleId,date;
        public final double startKm,endKm;
        public final String note;
        Trip(long id,long vehicleId,long date,double start,double end,String note) {
            this.id=id;this.vehicleId=vehicleId;this.date=date;this.startKm=start;this.endKm=end;this.note=note;
        }
    }
    public static final class Economy {
        public final double litersPer100Km,totalDistanceKm,totalLiters;
        public final List<Double> intervalValues;
        Economy(double km,double liters,List<Double> values) {
            totalDistanceKm=km;
            totalLiters=liters;
            litersPer100Km=km>0 ? 100d*liters/km : Double.NaN;
            intervalValues=Collections.unmodifiableList(values);
        }
    }

    public List<Vehicle> vehicles() {
        ArrayList<Vehicle> result=new ArrayList<>();
        try(Cursor c=getReadableDatabase().rawQuery("SELECT id,name,make,model FROM vehicles ORDER BY id DESC",null)) {
            while(c.moveToNext()) result.add(new Vehicle(c.getLong(0),c.getString(1),c.getString(2),c.getString(3)));
        }
        return result;
    }
    public long addVehicle(String name,String make,String model) {
        ContentValues v=new ContentValues();
        v.put("name",name.trim());v.put("make",make.trim());v.put("model",model.trim());
        return getWritableDatabase().insertOrThrow("vehicles",null,v);
    }
    public void deleteVehicle(long id) {
        getWritableDatabase().delete("vehicles","id=?",new String[]{Long.toString(id)});
    }
    public List<Fuel> fuel(long vehicleId) {
        ArrayList<Fuel> result=new ArrayList<>();
        try(Cursor c=getReadableDatabase().query("fuel",
            new String[]{"id","vehicle_id","date","odometer","liters","cost","full_tank"},
            "vehicle_id=?",new String[]{Long.toString(vehicleId)},null,null,"date DESC,id DESC")) {
            while(c.moveToNext()) result.add(new Fuel(c.getLong(0),c.getLong(1),c.getLong(2),c.getDouble(3),c.getDouble(4),c.getDouble(5),c.getInt(6)==1));
        }
        return result;
    }
    public long addFuel(long vehicleId,long date,double odo,double liters,double cost,boolean full) {
        if(odo<0 || liters<=0 || cost<0 || !Double.isFinite(odo) || !Double.isFinite(liters) || !Double.isFinite(cost)) throw new IllegalArgumentException("Invalid fuel values");
        ContentValues v=new ContentValues();v.put("vehicle_id",vehicleId);v.put("date",date);v.put("odometer",odo);
        v.put("liters",liters);v.put("cost",cost);v.put("full_tank",full?1:0);
        return getWritableDatabase().insertOrThrow("fuel",null,v);
    }
    public void deleteFuel(long id) {
        getWritableDatabase().delete("fuel","id=?",new String[]{Long.toString(id)});
    }
    public List<Expense> expenses(long vehicleId) {
        ArrayList<Expense> result=new ArrayList<>();
        try(Cursor c=getReadableDatabase().query("expenses",
            new String[]{"id","vehicle_id","date","category","amount","odometer","note"},
            "vehicle_id=?",new String[]{Long.toString(vehicleId)},null,null,"date DESC,id DESC")) {
            while(c.moveToNext()) result.add(new Expense(c.getLong(0),c.getLong(1),c.getLong(2),c.getString(3),c.getDouble(4),c.getDouble(5),c.getString(6)));
        }
        return result;
    }
    public long addExpense(long vehicleId,long date,String category,double amount,double odo,String note) {
        if(amount<0||odo<0||!Double.isFinite(amount)||!Double.isFinite(odo)) throw new IllegalArgumentException("Invalid expense values");
        ContentValues v=new ContentValues();v.put("vehicle_id",vehicleId);v.put("date",date);
        v.put("category",category.trim());v.put("amount",amount);v.put("odometer",odo);v.put("note",note.trim());
        return getWritableDatabase().insertOrThrow("expenses",null,v);
    }
    public void deleteExpense(long id) {
        getWritableDatabase().delete("expenses","id=?",new String[]{Long.toString(id)});
    }
    public List<Trip> trips(long vehicleId) {
        ArrayList<Trip> result=new ArrayList<>();
        try(Cursor c=getReadableDatabase().query("trips",
            new String[]{"id","vehicle_id","date","start_km","end_km","note"},
            "vehicle_id=?",new String[]{Long.toString(vehicleId)},null,null,"date DESC,id DESC")) {
            while(c.moveToNext()) result.add(new Trip(c.getLong(0),c.getLong(1),c.getLong(2),c.getDouble(3),c.getDouble(4),c.getString(5)));
        }
        return result;
    }
    public long addTrip(long vehicleId,long date,double start,double end,String note) {
        if(start<0||end<=start||!Double.isFinite(start)||!Double.isFinite(end)) throw new IllegalArgumentException("Trip ending odometer must exceed start");
        ContentValues v=new ContentValues();v.put("vehicle_id",vehicleId);v.put("date",date);
        v.put("start_km",start);v.put("end_km",end);v.put("note",note.trim());
        return getWritableDatabase().insertOrThrow("trips",null,v);
    }
    public void deleteTrip(long id) {
        getWritableDatabase().delete("trips","id=?",new String[]{Long.toString(id)});
    }

    /**
     * Independent, conservative full-to-full economy calculation.
     * The starting full fill-up is excluded from fuel consumed over the next
     * interval; partial purchases between consecutive full fill-ups are included.
     */
    public Economy economy(long vehicleId) {
        List<Fuel> ordered=new ArrayList<>(fuel(vehicleId));
        ordered.sort(Comparator.comparingDouble((Fuel f)->f.odometer).thenComparingLong(f->f.date).thenComparingLong(f->f.id));
        Fuel anchor=null;
        double sinceAnchor=0,kmTotal=0,litersTotal=0;
        ArrayList<Double> values=new ArrayList<>();
        for(Fuel f:ordered) {
            if(anchor==null) {
                if(f.full) {anchor=f;sinceAnchor=0;}
                continue;
            }
            if(f.odometer<=anchor.odometer) continue;
            sinceAnchor+=f.liters;
            if(f.full) {
                double km=f.odometer-anchor.odometer;
                if(km>0.1 && sinceAnchor>0) {
                    kmTotal+=km;
                    litersTotal+=sinceAnchor;
                    values.add(100d*sinceAnchor/km);
                }
                anchor=f;
                sinceAnchor=0;
            }
        }
        return new Economy(kmTotal,litersTotal,values);
    }
}
